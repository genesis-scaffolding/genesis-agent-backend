"""``config.yaml`` generation from catalog + recipes + overrides."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ...models import Catalog, ModelEntry
from ...paths import repo_root
from .recipes import Recipe

# Repo-root resolution so recipes can use paths like "vendor/llama.cpp/build/bin/llama-server".
REPO_ROOT = repo_root()

# Resource policy thresholds (bytes). When a model's weight size exceeds
# one of these, the corresponding VRAM-saver flag is added. These are
# machine-dependent (would change if you swapped GPUs), not model-
# dependent, so they live in code rather than in recipes.yaml.
DEFAULT_KV_QUANT_OVER = 25_000_000_000  # add -ctk q8_0 -ctv q8_0 above this
DEFAULT_MMPROJ_OFFLOAD_OVER = 25_000_000_000  # add --no-mmproj-offload above this
DEFAULT_BINARY_REL = "vendor/llama.cpp/build/bin/llama-server"


@dataclass(frozen=True)
class BuildThresholds:
    """Per-invocation thresholds. Defaults are the repo's historical values."""

    kv_quant_over: int = DEFAULT_KV_QUANT_OVER
    mmproj_offload_over: int = DEFAULT_MMPROJ_OFFLOAD_OVER
    default_binary_rel: str = DEFAULT_BINARY_REL


_SAMPLING_FLAGS: dict[str, str] = {
    "temp": "--temp",
    "top_p": "--top-p",
    "top_k": "--top-k",
    "min_p": "--min-p",
    "presence_penalty": "--presence-penalty",
    "repeat_penalty": "--repeat-penalty",
}


# ---------------------------------------------------------------------------
# Helpers (lifted from bin/build-config.py)
# ---------------------------------------------------------------------------


def _opt(recipe: Recipe, default_recipe: Recipe | None, key: str) -> Any:
    """Resolve an option: recipe's value if set, else default recipe's.

    The ``default`` recipe acts as a single source of truth for shared
    knobs (``ctx_min``, ``parallel``, sampling, etc.) so individual
    recipes only need to mention what they override.
    """
    val = getattr(recipe, key, None)
    if val is not None and val != {} and val != []:
        return val
    if default_recipe is not None:
        val = getattr(default_recipe, key, None)
        if val is not None and val != {} and val != []:
            return val
    return None


def _resolve_binary(binary: str) -> str:
    """Resolve a binary path: relative paths joined with REPO_ROOT."""
    p = Path(binary)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return str(p.resolve())


# ---------------------------------------------------------------------------
# File auto-detection
# ---------------------------------------------------------------------------


def _is_llm_candidate(entry: ModelEntry, source: str) -> bool:
    """Skip non-LLMs: image-gen / adapters / safetensors-only HF / empty."""
    if any("no model weights on disk" in n for n in entry.notes):
        return False
    if not entry.pieces:
        return False
    if source == "huggingface":
        return any(p.filename.lower().endswith(".gguf") for p in entry.pieces)
    return any(p.role not in ("config",) for p in entry.pieces)


@dataclass(frozen=True)
class DetectedFiles:
    """Auto-detected file paths for one model entry."""

    main: Path | None
    mmproj: Path | None
    draft: Path | None
    is_mtp: bool
    weight_bytes: int


def detect_files(entry: ModelEntry) -> DetectedFiles:
    """Pick main / mmproj / draft paths from a catalog entry's pieces.

    For ``main`` we choose the largest piece (handles Q4+Q6 siblings in
    the same repo by preferring the bigger quant).
    """
    mains = [p for p in entry.pieces if p.role == "main"]
    mmprojs = [p for p in entry.pieces if p.role == "mmproj"]
    drafts = [p for p in entry.pieces if p.role == "mtp"]
    main = max(mains, key=lambda p: p.bytes).path if mains else None
    has_mtp = (
        bool(drafts)
        or "mtp" in entry.name.lower()
        or any("mtp" in p.filename.lower() for p in mains)
    )
    return DetectedFiles(
        main=main,
        mmproj=mmprojs[0].path if mmprojs else None,
        draft=drafts[0].path if drafts else None,
        is_mtp=has_mtp,
        weight_bytes=entry.total_bytes,
    )


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


def build_cmd(
    recipe: Recipe,
    files: DetectedFiles,
    *,
    default_recipe: Recipe | None = None,
    binary_override: str | None = None,
    thresholds: BuildThresholds | None = None,
    overrides: dict[str, Any] | None = None,
) -> str:
    """Compose the llama-server command line for one model entry.

    Returns a multi-line shell string with backslash continuations.
    Options not set on ``recipe`` fall back to ``default_recipe``;
    options present in ``overrides`` win over both.

    Binary resolution order: recipe.binary -> CLI --binary ->
    default.binary -> thresholds.default_binary_rel.
    """
    ovr = overrides or {}
    thresholds = thresholds or BuildThresholds()
    binary_str = (
        recipe.binary
        or binary_override
        or (default_recipe.binary if default_recipe else None)
        or thresholds.default_binary_rel
    )
    resolved_binary = _resolve_binary(binary_str)

    sections: list[str] = []
    sections.append(f"{resolved_binary} \\")
    sections.append(f"  --model {files.main} \\")

    if files.mmproj:
        sections.append(f"  --mmproj {files.mmproj} \\")
        mmproj_offload = ovr.get("mmproj_offload", recipe.mmproj_offload)
        if mmproj_offload is None and default_recipe:
            mmproj_offload = default_recipe.mmproj_offload
        if mmproj_offload is True:
            offload = True
        elif mmproj_offload is False:
            offload = False
        else:
            offload = files.weight_bytes > thresholds.mmproj_offload_over
        if offload:
            sections.append("  --no-mmproj-offload \\")

    spec = ovr.get("spec", recipe.spec) or {}
    if isinstance(spec, dict) and spec.get("type") == "draft-mtp" and files.is_mtp:
        spec_parts = ["--spec-type", "draft-mtp"]
        if "n_max" in spec:
            spec_parts.extend(["--spec-draft-n-max", str(spec["n_max"])])
        if "p_min" in spec:
            spec_parts.extend(["--spec-draft-p-min", str(spec["p_min"])])
        sections.append("  " + " ".join(spec_parts) + " \\")
        if files.draft:
            sections.append(f"  --model-draft {files.draft} \\")

    runtime: list[str] = []
    kv_dtype = ovr.get("kv_cache", recipe.kv_cache)
    if kv_dtype is None and default_recipe:
        kv_dtype = default_recipe.kv_cache
    if kv_dtype in ("q8_0", "q4_0"):
        runtime.extend(["-ctk", kv_dtype, "-ctv", kv_dtype])
    elif files.weight_bytes > thresholds.kv_quant_over:
        runtime.extend(["-ctk", "q8_0", "-ctv", "q8_0"])

    ctx_min = ovr.get("ctx_min", recipe.ctx_min)
    if ctx_min is None and default_recipe:
        ctx_min = default_recipe.ctx_min
    if ctx_min is not None:
        runtime.extend(["--fit-ctx", str(ctx_min)])

    parallel = ovr.get("parallel", recipe.parallel)
    if parallel is None and default_recipe:
        parallel = default_recipe.parallel
    if parallel is not None:
        runtime.extend(["--parallel", str(parallel)])

    reasoning_budget = ovr.get("reasoning_budget", recipe.reasoning_budget)
    if reasoning_budget is None and default_recipe:
        reasoning_budget = default_recipe.reasoning_budget
    if reasoning_budget is not None and reasoning_budget >= 0:
        runtime.extend(["--reasoning-budget", str(reasoning_budget)])

    reasoning_budget_message = ovr.get(
        "reasoning_budget_message",
        recipe.reasoning_budget_message,
    )
    if reasoning_budget_message is None and default_recipe:
        reasoning_budget_message = default_recipe.reasoning_budget_message
    if reasoning_budget_message:
        runtime.extend(["--reasoning-budget-message", shlex.quote(reasoning_budget_message)])

    runtime.extend(["--jinja", "-fa", "on"])
    sections.append("  " + " ".join(runtime) + " \\")

    sections.append("  --port ${PORT} --host 127.0.0.1 \\")

    chat_template_file = ovr.get("chat_template_file", recipe.chat_template_file)
    if chat_template_file is None and default_recipe:
        chat_template_file = default_recipe.chat_template_file
    if chat_template_file:
        sections.append(f"  --chat-template-file {chat_template_file} \\")

    sampling = ovr.get("sampling", recipe.sampling) or {}
    sampling_parts: list[str] = []
    for k, flag in _SAMPLING_FLAGS.items():
        if k in sampling:
            sampling_parts.extend([flag, str(sampling[k])])
    if sampling_parts:
        sections.append("  " + " ".join(sampling_parts) + " \\")

    ctk = ovr.get("chat_template_kwargs", recipe.chat_template_kwargs)
    if ctk:
        ctk_json = json.dumps(ctk, separators=(",", ":"))
        sections.append(f"  --chat-template-kwargs '{ctk_json}'")

    if sections and sections[-1].endswith(" \\"):
        sections[-1] = sections[-1][:-2]

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Entry ID + display name
# ---------------------------------------------------------------------------


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

    src_short = "hf" if source == "huggingface" else "lms"
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
    base = name.split("/", 1)[-1]
    if multi_match:
        suffix = recipe.name.split(".")[-1].split("-")[-1]
        return f"{base} ({suffix})"
    return base


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------


def build_entry(
    entry: ModelEntry,
    recipe: Recipe,
    *,
    source: str,
    all_ids: set[str],
    multi_match: bool,
    default_recipe: Recipe | None = None,
    binary_override: str | None = None,
    thresholds: BuildThresholds | None = None,
    entry_id_override: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[str, dict]:
    files = detect_files(entry)
    if entry_id_override is None:
        entry_id = make_entry_id(
            entry.name,
            recipe,
            multi_match=multi_match,
            all_ids=all_ids,
            source=source,
        )
    else:
        entry_id = entry_id_override
    display = make_display_name(entry.name, recipe, multi_match)
    cmd = build_cmd(
        recipe,
        files,
        default_recipe=default_recipe,
        binary_override=binary_override,
        thresholds=thresholds or BuildThresholds(),
        overrides=overrides,
    )
    return entry_id, {
        "name": display,
        "cmd": cmd + "\n",
        "proxy": "http://127.0.0.1:${PORT}",
        "ttl": 0,
        "resolved_from": recipe.name,
    }


def build_config(
    catalog: Catalog,
    recipes,
    overrides: dict[str, dict] | None = None,
    *,
    binary_override: str | None = None,
    thresholds: BuildThresholds | None = None,
) -> list[tuple[str, dict]]:
    """Walk the catalog, match recipes, apply overrides, emit entries.

    ``recipes`` is a :class:`Recipes` object (the ``default`` and
    ``matchable`` lists live there). ``overrides`` is the
    ``{entry_id: {field: value}}`` dict from :class:`OverridesStore`.
    """
    overrides = overrides or {}
    thresholds = thresholds or BuildThresholds()
    entries: list[tuple[str, dict]] = []
    all_ids: set[str] = set()

    for source_key, entries_for_source in catalog.by_source().items():
        for entry in entries_for_source:
            if not _is_llm_candidate(entry, source_key):
                continue
            resolved = recipes.resolve(entry.name)
            if not resolved.matched:
                continue
            multi = len(resolved.matched) > 1

            for recipe in resolved.matched:
                # Compute the entry_id first so we can look up overrides
                # keyed by it. make_entry_id mutates all_ids as a side
                # effect, so calling it before passing overrides is the
                # only correct order.
                entry_id = make_entry_id(
                    entry.name,
                    recipe,
                    multi_match=multi,
                    all_ids=all_ids,
                    source=source_key,
                )
                _, data = build_entry(
                    entry,
                    recipe,
                    source=source_key,
                    all_ids=all_ids,
                    multi_match=multi,
                    default_recipe=recipes.default,
                    binary_override=binary_override,
                    thresholds=thresholds,
                    entry_id_override=entry_id,
                    overrides=overrides.get(entry_id),
                )
                entries.append((entry_id, data))

    return entries


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
    # YAML field form first (newer writers, more reliable).
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        raw = None
    if isinstance(raw, dict):
        value = raw.get("generated_at")
        if isinstance(value, str):
            return value
    # Legacy header comment form (bin/build-config.py, until Phase 10).
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
    "BuildThresholds",
    "DetectedFiles",
    "build_cmd",
    "build_config",
    "build_entry",
    "detect_files",
    "emit_payload",
    "is_config_stale",
    "make_display_name",
    "make_entry_id",
    "read_generated_at",
    "write_config",
]
