"""config.yaml generation: catalog + recipes + overrides → structured config + cmd.

This module owns the whole pipeline:

  1. :func:`walk_models` — filter LLM candidates, walk catalog, yield :class:`ModelMatch`
  2. :func:`evaluate_recipe` — resolve fields (override > recipe > default > computed),
     apply size-based fallbacks, render cmd
  3. :func:`cmd_from_evaluated` — pure cmd formatter from the structured fields

Both the YAML writer (:func:`build_config`) and the config editor UI
(:func:`evaluate_all`) consume from this single pipeline.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from ...contracts import Catalog, ModelEntry
from .recipes import BUNDLED_RECIPES_PATH, Recipe, Recipes

# Resource policy thresholds (bytes). When a model's weight size exceeds
# one of these, the corresponding VRAM-saver flag is added. These are
# machine-dependent (would change if you swapped GPUs), not model-
# dependent, so they live in code rather than in recipes.yaml.
DEFAULT_KV_QUANT_OVER = 25_000_000_000  # add -ctk q8_0 -ctv q8_0 above this
DEFAULT_MMPROJ_OFFLOAD_OVER = 25_000_000_000  # add --no-mmproj-offload above this
DEFAULT_BINARY_REL = "vendor/llama.cpp/build/bin/llama-server"

_SAMPLING_FLAGS: dict[str, str] = {
    "temp": "--temp",
    "top_p": "--top-p",
    "top_k": "--top-k",
    "min_p": "--min-p",
    "presence_penalty": "--presence-penalty",
    "repeat_penalty": "--repeat-penalty",
}


# ===========================================================================
# Types
# ===========================================================================


@dataclass(frozen=True)
class BuildOptions:
    """Per-invocation build policy. The service supplies these from its context.

    ``repo_root`` has no default on purpose: relative binary paths in recipes resolve
    against it, and a plugin cannot know where the checkout lives. The framework does.

    ``default_binary`` is the resolved framework-managed llama-server binary path
    (chosen via the service's variant setting). It beats the default recipe's
    bundled ``binary`` field but loses to per-model recipe ``binary`` overrides
    (e.g. bonsai → prism-llama.cpp). ``default_binary_rel`` stays as the final
    safety net for users who haven't migrated to the variant workflow.
    """

    repo_root: Path
    kv_quant_over: int = DEFAULT_KV_QUANT_OVER
    mmproj_offload_over: int = DEFAULT_MMPROJ_OFFLOAD_OVER
    default_binary_rel: str = DEFAULT_BINARY_REL
    default_binary: str | None = None


@dataclass(frozen=True)
class DetectedFileSet:
    """Auto-detected file paths for one GGUF piece.

    ``main`` and ``piece_bytes`` are per-piece; ``mmproj``, ``draft``,
    and ``is_mtp`` are shared across all pieces in the same entry.

    ``filename`` is the symlink name from the catalog (e.g.
    ``LFM2.5-VL-3B-UD-Q8_K_XL.gguf``), distinct from ``main``
    which is the resolved blob path. Use ``filename`` for entry
    naming; use ``main`` for the llama-server cmd.
    """

    main: Path | None
    filename: str
    mmproj: Path | None
    draft: Path | None
    is_mtp: bool
    weight_bytes: int


class FieldSource(StrEnum):
    """Where an evaluated field's effective value came from."""

    OVERRIDE = "override"  # from overrides.yaml
    RECIPE = "recipe"  # from the matched recipe
    DEFAULT = "default"  # from Recipes.default (fallback recipe)
    COMPUTED = "computed"  # builder's size-based fallback (kv_cache, mmproj_offload)


@dataclass(frozen=True)
class EvaluatedConfig:
    """Effective config for one model: every overridable field, with provenance."""

    name: str
    entry_id: str
    matched_recipe: str | None
    binary: str
    files: DetectedFileSet

    kv_cache: str | None
    mmproj_offload: bool | None
    spec: dict[str, Any] | None
    ctx_min: int | None
    parallel: int | None
    reasoning_budget: int | None
    reasoning_budget_message: str | None
    chat_template_file: str | None
    sampling: dict[str, Any]
    chat_template_kwargs: dict[str, Any]

    provenance: dict[str, FieldSource]
    cmd: str

    extra_flags: tuple[str, ...] = ()
    ctx_size: int | None = None
    hardcoded_flags: tuple[str, ...] = (
        "--kv-unified",
        "--jinja",
        "-fa on",
        "--port ${PORT}",
        "--host 127.0.0.1",
    )


@dataclass(frozen=True)
class ModelMatch:
    """One catalog entry * one matched recipe * one GGUF piece - what the walker yields."""

    entry_id: str
    entry: ModelEntry
    source: str
    recipe: Recipe
    multi_match: bool
    files: DetectedFileSet
    entry_overrides: dict[str, Any] | None


# ===========================================================================
# Catalog walk helpers
# ===========================================================================


def _is_llm_candidate(entry: ModelEntry, source: str) -> bool:
    """Skip non-LLMs: image-gen / adapters / safetensors-only HF / empty."""
    if any("no model weights on disk" in n for n in entry.notes):
        return False
    if not entry.pieces:
        return False
    if source == "huggingface":
        return any(p.filename.lower().endswith(".gguf") for p in entry.pieces)
    return any(p.role not in ("config",) for p in entry.pieces)


def short_source_label(source: str) -> str:
    """Stable short label for entry IDs. Strip non-alphanumerics and
    lowercase, then take the first three characters.

    Deterministic across runs and over arbitrarily-named future
    sources. Historically ``"huggingface"`` and ``"lmstudio"`` had
    hand-chosen labels (``"hf"``, ``"lms"``); after ADR-011 the labels
    are derived, so they differ. That's fine — entry IDs regenerate
    on every config rebuild.
    """
    if not source:
        return "x"
    cleaned = "".join(c for c in source.lower() if c.isalnum())
    return cleaned[:3] or "x"


def detect_file_sets(entry: ModelEntry) -> list[DetectedFileSet]:
    """Return one DetectedFileSet per main (GGUF) piece in the entry.

    Each set shares the same mmproj/draft/is_mtp and has its own
    main path and piece-bytes. An entry with N GGUF files yields N
    file sets.
    """
    mains = [p for p in entry.pieces if p.role == "main"]
    mmprojs = [p for p in entry.pieces if p.role == "mmproj"]
    drafts = [p for p in entry.pieces if p.role == "mtp"]
    has_mtp = (
        bool(drafts)
        or "mtp" in entry.name.lower()
        or any("mtp" in p.filename.lower() for p in mains)
    )
    base = DetectedFileSet(
        main=None,
        filename="",
        mmproj=mmprojs[0].path if mmprojs else None,
        draft=drafts[0].path if drafts else None,
        is_mtp=has_mtp,
        weight_bytes=0,
    )
    return [
        DetectedFileSet(
            main=p.path,
            filename=p.filename,
            mmproj=base.mmproj,
            draft=base.draft,
            is_mtp=base.is_mtp,
            weight_bytes=p.bytes,
        )
        for p in mains
    ]


def _resolve_binary(binary: str, repo_root: Path) -> str:
    """Relative binaries resolve against the checkout root."""
    p = Path(binary)
    if not p.is_absolute():
        p = repo_root / p
    return str(p.resolve())


def _resolve_chat_template_file(path: str) -> str:
    """Relative chat_template_file paths resolve from the bundled recipes directory.

    Absolute paths (user overrides) pass through unchanged.
    """
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str((BUNDLED_RECIPES_PATH.parent / p).resolve())


def make_entry_id(
    name: str,
    recipe: Recipe,
    *,
    multi_match: bool,
    all_ids: set[str],
    source: str,
) -> str:
    """Sanitize catalog name to YAML key. On collision: publisher prefix,
    then source suffix (hf/lms), then a numeric counter."""
    base = name.split("/", 1)[-1]
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    if multi_match:
        suffix = recipe.name.split(".")[-1].split("-")[-1]
        base = f"{base}-{suffix}"

    if base not in all_ids:
        all_ids.add(base)
        return base

    pub = name.split("/", 1)[0] if "/" in name else ""
    pub_clean = re.sub(r"[^a-z0-9]+", "-", pub.lower()).strip("-")
    if pub_clean:
        candidate = f"{pub_clean}-{base}"
        if candidate not in all_ids:
            all_ids.add(candidate)
            return candidate

    src_short = short_source_label(source)
    candidate = f"{base}-{src_short}"
    if candidate not in all_ids:
        all_ids.add(candidate)
        return candidate

    n = 2
    while f"{base}-{n}" in all_ids:
        n += 1
    candidate = f"{base}-{n}"
    all_ids.add(candidate)
    return candidate


def make_display_name(name: str, recipe: Recipe, multi_match: bool) -> str:
    """Derive the display name shown in llama-swap UI from a piece filename.

    The input is always a piece filename (not a model name). The
    ``.gguf`` extension is stripped if present.
    """
    base = name.split("/", 1)[-1]
    if base.lower().endswith(".gguf"):
        base = base[:-5]
    if multi_match:
        suffix = recipe.name.split(".")[-1].split("-")[-1]
        return f"{base} ({suffix})"
    return base


def walk_models(
    catalog: Catalog,
    recipes: Recipes,
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
    binary_override: str | None = None,
    options: BuildOptions,
) -> Iterator[ModelMatch]:
    """Walk catalog with same filter as config.yaml. Yields per-model-match.

    This is the single source of truth for "which entries make it into
    config.yaml". Both :func:`build_config` (yaml writer) and
    :func:`evaluate_all` (config editor UI) iterate from this.
    """
    ovr = overrides or {}
    all_ids: set[str] = set()

    for source_key, entries_for_source in catalog.by_source().items():
        for entry in entries_for_source:
            if not _is_llm_candidate(entry, source_key):
                continue
            resolved = recipes.resolve(entry.name)
            if not resolved.matched:
                continue
            multi = len(resolved.matched) > 1

            for file_set in detect_file_sets(entry):
                for recipe in resolved.matched:
                    piece_name = file_set.filename
                    # Strip .gguf so it doesn't become "-gguf" in the YAML key.
                    if piece_name.lower().endswith(".gguf"):
                        piece_name = piece_name[:-5]
                    entry_id = make_entry_id(
                        piece_name,
                        recipe,
                        multi_match=multi,
                        all_ids=all_ids,
                        source=source_key,
                    )
                    yield ModelMatch(
                        entry_id=entry_id,
                        entry=entry,
                        source=source_key,
                        recipe=recipe,
                        multi_match=multi,
                        files=file_set,
                        entry_overrides=ovr.get(entry_id),
                    )


# ===========================================================================
# Field resolution (provenance)
# ===========================================================================


def _source_simple(
    ovr: dict[str, Any], recipe: Recipe, default: Recipe | None, key: str
) -> FieldSource:
    """Source for a field that resolves via override > recipe > default."""
    if key in ovr:
        return FieldSource.OVERRIDE
    val = getattr(recipe, key, None)
    if val is not None and val != {} and val != []:
        return FieldSource.RECIPE
    if default is not None:
        val = getattr(default, key, None)
        if val is not None and val != {} and val != []:
            return FieldSource.DEFAULT
    return FieldSource.COMPUTED


def _source_recipe_only(ovr: dict[str, Any], recipe: Recipe, key: str) -> FieldSource:
    """Source for fields that never fall back from recipe to default."""
    if key in ovr:
        return FieldSource.OVERRIDE
    return FieldSource.RECIPE


# ===========================================================================
# Evaluation pipeline
# ===========================================================================


def evaluate_recipe(
    recipe: Recipe,
    files: DetectedFileSet,
    *,
    entry_id: str,
    name: str,
    default_recipe: Recipe | None = None,
    binary_override: str | None = None,
    options: BuildOptions,
    overrides: dict[str, Any] | None = None,
) -> EvaluatedConfig:
    """Resolve every overridable field, apply fallbacks, render cmd.

    Precedence: ``override > recipe > default_recipe > computed``.
    Size-based fallbacks for ``kv_cache`` and ``mmproj_offload`` fire
    here so the rendered cmd and the structured view never disagree.
    """
    ovr = overrides or {}

    # --- binary (5-level) ---
    binary_str = (
        ovr.get("binary")
        or recipe.binary
        or options.default_binary
        or binary_override
        or (default_recipe.binary if default_recipe else None)
        or options.default_binary_rel
    )
    resolved_binary = _resolve_binary(binary_str, options.repo_root)
    if "binary" in ovr:
        binary_source = FieldSource.OVERRIDE
    elif recipe.binary:
        binary_source = FieldSource.RECIPE
    elif options.default_binary or binary_override:
        binary_source = FieldSource.COMPUTED
    elif default_recipe and default_recipe.binary:
        binary_source = FieldSource.DEFAULT
    else:
        binary_source = FieldSource.COMPUTED

    # --- simple fields (parallel, ctx_min, etc.) ---
    parallel = ovr.get("parallel", recipe.parallel)
    if parallel is None and default_recipe:
        parallel = default_recipe.parallel

    ctx_min = ovr.get("ctx_min", recipe.ctx_min)
    if ctx_min is None and default_recipe:
        ctx_min = default_recipe.ctx_min

    ctx_size = ovr.get("ctx_size", recipe.ctx_size)
    if ctx_size is None and default_recipe:
        ctx_size = default_recipe.ctx_size

    reasoning_budget = ovr.get("reasoning_budget", recipe.reasoning_budget)
    if reasoning_budget is None and default_recipe:
        reasoning_budget = default_recipe.reasoning_budget

    reasoning_budget_message = ovr.get("reasoning_budget_message", recipe.reasoning_budget_message)
    if reasoning_budget_message is None and default_recipe:
        reasoning_budget_message = default_recipe.reasoning_budget_message

    chat_template_file = ovr.get("chat_template_file", recipe.chat_template_file)
    if chat_template_file is None and default_recipe:
        chat_template_file = default_recipe.chat_template_file

    spec = ovr.get("spec", recipe.spec)

    # --- kv_cache with size-based fallback ---
    kv_cache = ovr.get("kv_cache", recipe.kv_cache)
    if kv_cache is None and default_recipe:
        kv_cache = default_recipe.kv_cache
    if kv_cache is None and files.weight_bytes > options.kv_quant_over:
        kv_cache = "q8_0"

    # --- mmproj_offload with size-based fallback ---
    mmproj_offload = ovr.get("mmproj_offload", recipe.mmproj_offload)
    if mmproj_offload is None and default_recipe:
        mmproj_offload = default_recipe.mmproj_offload
    if mmproj_offload is None and files.weight_bytes > options.mmproj_offload_over:
        mmproj_offload = True

    # --- sampling / chat_template_kwargs (no default fallback) ---
    sampling = ovr.get("sampling", recipe.sampling) or {}
    chat_template_kwargs = ovr.get("chat_template_kwargs", recipe.chat_template_kwargs)

    extra_flags: list[str] = ovr.get("extra_flags", list(recipe.extra_flags))
    if not extra_flags and default_recipe:
        extra_flags = list(default_recipe.extra_flags)

    provenance: dict[str, FieldSource] = {
        "binary": binary_source,
        "kv_cache": _source_simple(ovr, recipe, default_recipe, "kv_cache"),
        "mmproj_offload": _source_simple(ovr, recipe, default_recipe, "mmproj_offload"),
        "spec": _source_simple(ovr, recipe, default_recipe, "spec"),
        "ctx_min": _source_simple(ovr, recipe, default_recipe, "ctx_min"),
        "ctx_size": _source_simple(ovr, recipe, default_recipe, "ctx_size"),
        "parallel": _source_simple(ovr, recipe, default_recipe, "parallel"),
        "reasoning_budget": _source_simple(ovr, recipe, default_recipe, "reasoning_budget"),
        "reasoning_budget_message": _source_simple(
            ovr, recipe, default_recipe, "reasoning_budget_message"
        ),
        "chat_template_file": _source_simple(ovr, recipe, default_recipe, "chat_template_file"),
        "sampling": _source_recipe_only(ovr, recipe, "sampling"),
        "chat_template_kwargs": _source_recipe_only(ovr, recipe, "chat_template_kwargs"),
        "extra_flags": _source_recipe_only(ovr, recipe, "extra_flags"),
    }

    cmd = cmd_from_evaluated_dict(
        binary=resolved_binary,
        files=files,
        kv_cache=kv_cache,
        mmproj_offload=mmproj_offload,
        spec=spec,
        ctx_min=ctx_min,
        ctx_size=ctx_size,
        parallel=parallel,
        reasoning_budget=reasoning_budget,
        reasoning_budget_message=reasoning_budget_message,
        chat_template_file=chat_template_file,
        sampling=sampling,
        chat_template_kwargs=chat_template_kwargs,
        extra_flags=tuple(extra_flags),
        provenance=provenance,
    )

    return EvaluatedConfig(
        name=name,
        entry_id=entry_id,
        matched_recipe=recipe.name,
        binary=resolved_binary,
        files=files,
        kv_cache=kv_cache,
        mmproj_offload=mmproj_offload,
        spec=spec,
        ctx_min=ctx_min,
        ctx_size=ctx_size,
        parallel=parallel,
        reasoning_budget=reasoning_budget,
        reasoning_budget_message=reasoning_budget_message,
        chat_template_file=chat_template_file,
        sampling=sampling,
        chat_template_kwargs=chat_template_kwargs,
        provenance=provenance,
        cmd=cmd,
    )


def cmd_from_evaluated(evaluated: EvaluatedConfig) -> str:
    """Format the cmd string from an EvaluatedConfig (convenience wrapper)."""
    return cmd_from_evaluated_dict(
        binary=evaluated.binary,
        files=evaluated.files,
        kv_cache=evaluated.kv_cache,
        mmproj_offload=evaluated.mmproj_offload,
        spec=evaluated.spec,
        ctx_min=evaluated.ctx_min,
        ctx_size=evaluated.ctx_size,
        parallel=evaluated.parallel,
        reasoning_budget=evaluated.reasoning_budget,
        reasoning_budget_message=evaluated.reasoning_budget_message,
        chat_template_file=evaluated.chat_template_file,
        sampling=evaluated.sampling,
        chat_template_kwargs=evaluated.chat_template_kwargs,
        provenance=evaluated.provenance,
    )


def cmd_from_evaluated_dict(
    *,
    binary: str,
    files: DetectedFileSet,
    kv_cache: str | None,
    mmproj_offload: bool | None,
    spec: dict[str, Any] | None,
    ctx_min: int | None,
    ctx_size: int | None,
    parallel: int | None,
    reasoning_budget: int | None,
    reasoning_budget_message: str | None,
    chat_template_file: str | None,
    sampling: dict[str, Any],
    chat_template_kwargs: dict[str, Any] | None,
    provenance: dict[str, FieldSource] | None = None,
    extra_flags: tuple[str, ...] = (),
) -> str:
    """Format the llama-server cmd string from resolved fields.

    Reads the structured fields directly (no re-resolution). Conditional
    emission depends on the files (``--no-mmproj-offload`` only when an
    mmproj is present; ``--spec-type draft-mtp`` only when files indicate
    MTP support).
    """
    sections: list[str] = []
    sections.append(f"{binary} \\")
    sections.append(f"  --model {files.main} \\")

    if files.mmproj:
        sections.append(f"  --mmproj {files.mmproj} \\")

    if files.mmproj and mmproj_offload is True:
        sections.append("  --no-mmproj-offload \\")

    if (
        spec
        and isinstance(spec, dict)
        and spec.get("type") == "draft-mtp"
        and (
            files.is_mtp
            or (provenance is not None and provenance.get("spec") == FieldSource.OVERRIDE)
        )
    ):
        spec_parts = ["--spec-type", "draft-mtp"]
        if "n_max" in spec:
            spec_parts.extend(["--spec-draft-n-max", str(spec["n_max"])])
        if "p_min" in spec:
            spec_parts.extend(["--spec-draft-p-min", str(spec["p_min"])])
        sections.append("  " + " ".join(spec_parts) + " \\")
        if files.draft:
            sections.append(f"  --model-draft {files.draft} \\")

    runtime: list[str] = []
    if kv_cache in ("q8_0", "q4_0"):
        runtime.extend(["-ctk", kv_cache, "-ctv", kv_cache])

    if ctx_min is not None:
        runtime.extend(["--fit-ctx", str(ctx_min)])

    if ctx_size is not None:
        runtime.extend(["-c", str(ctx_size)])

    if parallel is not None:
        runtime.extend(["--parallel", str(parallel)])

    if reasoning_budget is not None and reasoning_budget >= 0:
        runtime.extend(["--reasoning-budget", str(reasoning_budget)])

    if reasoning_budget_message:
        runtime.extend(["--reasoning-budget-message", shlex.quote(reasoning_budget_message)])

    runtime.extend(["--kv-unified", "--jinja", "-fa", "on"])
    sections.append("  " + " ".join(runtime) + " \\")

    sections.append("  --port ${PORT} --host 127.0.0.1 \\")

    if chat_template_file:
        resolved = _resolve_chat_template_file(chat_template_file)
        sections.append(f"  --chat-template-file {resolved} \\")

    if sampling:
        sampling_parts: list[str] = []
        for k, flag in _SAMPLING_FLAGS.items():
            if k in sampling:
                sampling_parts.extend([flag, str(sampling[k])])
        if sampling_parts:
            sections.append("  " + " ".join(sampling_parts) + " \\")

    if chat_template_kwargs:
        ctk_json = json.dumps(chat_template_kwargs, separators=(",", ":"))
        sections.append(f"  --chat-template-kwargs '{ctk_json}'")

    if extra_flags:
        sections.append("  " + " \\\n  ".join(extra_flags) + " \\")

    if sections and sections[-1].endswith(" \\"):
        sections[-1] = sections[-1][:-2]

    return "\n".join(sections)


def evaluate_all(
    catalog: Catalog,
    recipes: Recipes,
    overrides: dict[str, dict[str, Any]] | None = None,
    *,
    binary_override: str | None = None,
    options: BuildOptions,
) -> dict[str, EvaluatedConfig]:
    """Evaluate every LLM catalog entry. Returns ``{entry_id: EvaluatedConfig}``.

    Uses the same filter as :func:`build_config`, so the UI sees only
    models that actually make it into config.yaml.
    """
    out: dict[str, EvaluatedConfig] = {}
    for match in walk_models(
        catalog,
        recipes,
        overrides=overrides,
        binary_override=binary_override,
        options=options,
    ):
        piece_name = match.files.filename
        evaluated = evaluate_recipe(
            match.recipe,
            match.files,
            entry_id=match.entry_id,
            name=make_display_name(piece_name, match.recipe, match.multi_match),
            default_recipe=recipes.default,
            binary_override=binary_override,
            options=options,
            overrides=match.entry_overrides,
        )
        out[match.entry_id] = evaluated
    return out


# ===========================================================================
# build_entry / build_config — YAML write path
# ===========================================================================


def build_entry(
    entry: ModelEntry,
    recipe: Recipe,
    file_set: DetectedFileSet,
    *,
    entry_id: str,
    multi_match: bool,
    default_recipe: Recipe | None = None,
    binary_override: str | None = None,
    options: BuildOptions,
    overrides: dict[str, Any] | None = None,
) -> tuple[str, dict]:
    """Build one config.yaml entry by evaluating the recipe.

    Returns ``(entry_id, data)`` where ``data`` is the dict that
    :func:`emit_payload` serializes.
    """
    piece_name = file_set.filename
    display = make_display_name(piece_name, recipe, multi_match)
    evaluated = evaluate_recipe(
        recipe,
        file_set,
        entry_id=entry_id,
        name=display,
        default_recipe=default_recipe,
        binary_override=binary_override,
        options=options,
        overrides=overrides,
    )
    return entry_id, {
        "name": evaluated.name,
        "cmd": evaluated.cmd + "\n",
        "proxy": "http://127.0.0.1:${PORT}",
        "ttl": 0,
        "resolved_from": evaluated.matched_recipe,
    }


def build_config(
    catalog: Catalog,
    recipes: Recipes,
    overrides: dict[str, dict[str, Any]] | None = None,
    *,
    binary_override: str | None = None,
    options: BuildOptions,
) -> list[tuple[str, dict]]:
    """Walk the catalog, match recipes, apply overrides, emit entries.

    Uses :func:`walk_models` as the filter (single source of truth for
    "which entries make it into config.yaml").
    """
    entries: list[tuple[str, dict]] = []
    for match in walk_models(
        catalog,
        recipes,
        overrides=overrides,
        binary_override=binary_override,
        options=options,
    ):
        _, data = build_entry(
            match.entry,
            match.recipe,
            match.files,
            entry_id=match.entry_id,
            multi_match=match.multi_match,
            default_recipe=recipes.default,
            binary_override=binary_override,
            options=options,
            overrides=match.entry_overrides,
        )
        entries.append((match.entry_id, data))
    return entries


# ===========================================================================
# YAML emission
# ===========================================================================


def emit_payload(
    entries: list[tuple[str, dict]],
    root: str,
    generated_at: str,
) -> dict:
    """Build the dict that ``yaml.safe_dump`` will serialize.

    ``cmd`` values are wrapped in :class:`_LiteralBlock` so PyYAML
    emits them as ``|`` literal block scalars (matching the format
    produced by ``bin/build-config.py``'s hand-rolled emitter). The
    structure matches what llama-swap's config loader expects.

    ``generated_at`` and ``root`` are embedded so the Streamlit config
    editor can show "stale" when the catalog has changed since
    ``config.yaml`` was last written (:func:`read_generated_at`). The
    fields are extra metadata not consumed by llama-swap; harmless to
    leave in place.
    """
    models = {}
    for entry_id, data in entries:
        d = dict(data)
        if "cmd" in d:
            d["cmd"] = _LiteralBlock(d["cmd"])
        models[entry_id] = d
    return {
        "healthCheckTimeout": 60,
        "logLevel": "info",
        "generated_at": generated_at,
        "root": root,
        "models": models,
    }


class _LiteralBlock(str):
    """Marker subclass that triggers PyYAML's literal-block representer."""


def _literal_block_representer(dumper, data):
    # PyYAML's represent_scalar signature is duck-typed; we declare the
    # param loosely so pyright doesn't complain about SafeDumper vs Dumper.
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


yaml.add_representer(_LiteralBlock, _literal_block_representer, Dumper=yaml.SafeDumper)


def write_config(
    path: Path, entries: list[tuple[str, dict]], *, root: str, generated_at: str
) -> bool:
    """Write ``config.yaml`` iff content differs. Returns True iff a write happened.

    Preserves the existing mtime on no-op rebuilds so llama-swap's
    ``-watch-config`` doesn't reload on a no-op (this is the same
    safety the old hand-rolled emitter had).
    """
    payload = emit_payload(entries, root, generated_at)
    text = yaml.safe_dump(
        payload,
        sort_keys=False,
        default_flow_style=False,
        width=10000,
    )
    try:
        existing = path.read_text()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return True
    if existing == text:
        return False
    path.write_text(text)
    return True


def read_generated_at(path: Path) -> str | None:
    """Read the ``generated_at`` timestamp embedded by :func:`emit_payload`.

    Two forms are recognized:

    1. The new form written by :func:`emit_payload`:
       ``generated_at: 'YYYY-MM-DDTHH:MM:SS+TZ'`` (YAML field).
    2. The legacy form written by ``bin/build-config.py`` (still in use
       via ``make all`` until Phase 10 retirement):
       ``# llama-swap config generated YYYY-MM-DDTHH:MM:SS+TZ`` (header
       comment).

    Returns None if the file is missing, malformed, or contains no
    timestamp in either form. The catalog-editor "stale" indicator
    treats None as stale-by-default.
    """
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        raw = None
    if isinstance(raw, dict):
        value = raw.get("generated_at")
        if isinstance(value, str):
            return value
    comment_match = re.search(
        r"^#\s*llama-swap config generated (\S+)\s*$",
        text,
        re.MULTILINE,
    )
    if comment_match is not None:
        return comment_match.group(1)
    return None


def is_config_stale(config_path: Path, *, catalog_generated_at: str) -> bool:
    """True iff ``config.yaml`` is older than the current catalog."""
    embedded = read_generated_at(config_path)
    if embedded is None:
        return True
    return embedded != catalog_generated_at


__all__ = [
    "DEFAULT_BINARY_REL",
    "DEFAULT_KV_QUANT_OVER",
    "DEFAULT_MMPROJ_OFFLOAD_OVER",
    "BuildOptions",
    "DetectedFileSet",
    "EvaluatedConfig",
    "FieldSource",
    "ModelMatch",
    "build_config",
    "build_entry",
    "cmd_from_evaluated",
    "cmd_from_evaluated_dict",
    "detect_file_sets",
    "evaluate_all",
    "evaluate_recipe",
    "is_config_stale",
    "make_display_name",
    "make_entry_id",
    "read_generated_at",
    "short_source_label",
    "walk_models",
    "write_config",
]
