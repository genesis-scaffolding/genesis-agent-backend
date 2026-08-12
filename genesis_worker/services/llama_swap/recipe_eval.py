"""Structured view of a recipe + files + overrides.

The cmd string is *output* of this data model (via :func:`build_cmd`).
The UI presents the structured fields directly so it never has to
parse cmd back out.

Field resolution mirrors :func:`build_cmd` exactly: ``override > recipe
> default_recipe > computed``. Provenance is tracked per field so the
UI can show where each value came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ...contracts import Catalog
from .generate_config import (
    BuildOptions,
    DetectedFiles,
    _resolve_binary,
    build_cmd,
    detect_files,
    make_display_name,
    make_entry_id,
)
from .recipes import Recipe, Recipes


class FieldSource(StrEnum):
    OVERRIDE = "override"
    RECIPE = "recipe"
    DEFAULT = "default"
    COMPUTED = "computed"


@dataclass(frozen=True)
class EvaluatedConfig:
    """Effective config for one model, computed from recipe + files + overrides."""

    name: str
    entry_id: str
    matched_recipe: str | None
    binary: str
    files: DetectedFiles

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

    hardcoded_flags: tuple[str, ...] = (
        "--jinja",
        "-fa on",
        "--port ${PORT}",
        "--host 127.0.0.1",
    )


def _has_override(ovr: dict[str, Any], key: str) -> bool:
    return key in ovr


def _source_simple(
    ovr: dict[str, Any], recipe: Recipe, default: Recipe | None, key: str
) -> FieldSource:
    """Source for a field that build_cmd resolves via override > recipe > default."""
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


def _source_recipe_only(
    ovr: dict[str, Any], recipe: Recipe, key: str
) -> FieldSource:
    """Source for fields that build_cmd never falls back from recipe to default."""
    if key in ovr:
        return FieldSource.OVERRIDE
    return FieldSource.RECIPE


def evaluate_recipe(
    recipe: Recipe,
    files: DetectedFiles,
    *,
    default_recipe: Recipe | None = None,
    binary_override: str | None = None,
    options: BuildOptions,
    overrides: dict[str, Any] | None = None,
) -> EvaluatedConfig:
    ovr = overrides or {}

    # --- binary (4-level fallback) ---
    binary_str = (
        ovr.get("binary")
        or recipe.binary
        or binary_override
        or (default_recipe.binary if default_recipe else None)
        or options.default_binary_rel
    )
    resolved_binary = _resolve_binary(binary_str, options.repo_root)
    if _has_override(ovr, "binary"):
        binary_source = FieldSource.OVERRIDE
    elif recipe.binary:
        binary_source = FieldSource.RECIPE
    elif binary_override:
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

    reasoning_budget = ovr.get("reasoning_budget", recipe.reasoning_budget)
    if reasoning_budget is None and default_recipe:
        reasoning_budget = default_recipe.reasoning_budget

    reasoning_budget_message = ovr.get(
        "reasoning_budget_message", recipe.reasoning_budget_message
    )
    if reasoning_budget_message is None and default_recipe:
        reasoning_budget_message = default_recipe.reasoning_budget_message

    chat_template_file = ovr.get("chat_template_file", recipe.chat_template_file)
    if chat_template_file is None and default_recipe:
        chat_template_file = default_recipe.chat_template_file

    spec = ovr.get("spec", recipe.spec)

    # --- kv_cache (size-based fallback if unset) ---
    kv_cache = ovr.get("kv_cache", recipe.kv_cache)
    if kv_cache is None and default_recipe:
        kv_cache = default_recipe.kv_cache

    # --- mmproj_offload (size-based fallback if unset) ---
    mmproj_offload = ovr.get("mmproj_offload", recipe.mmproj_offload)
    if mmproj_offload is None and default_recipe:
        mmproj_offload = default_recipe.mmproj_offload

    # --- sampling / chat_template_kwargs (build_cmd never falls back) ---
    sampling = ovr.get("sampling", recipe.sampling) or {}
    chat_template_kwargs = ovr.get("chat_template_kwargs", recipe.chat_template_kwargs)

    # --- provenance ---
    provenance: dict[str, FieldSource] = {
        "binary": binary_source,
        "kv_cache": _source_simple(ovr, recipe, default_recipe, "kv_cache"),
        "mmproj_offload": _source_simple(ovr, recipe, default_recipe, "mmproj_offload"),
        "spec": _source_simple(ovr, recipe, default_recipe, "spec"),
        "ctx_min": _source_simple(ovr, recipe, default_recipe, "ctx_min"),
        "parallel": _source_simple(ovr, recipe, default_recipe, "parallel"),
        "reasoning_budget": _source_simple(ovr, recipe, default_recipe, "reasoning_budget"),
        "reasoning_budget_message": _source_simple(
            ovr, recipe, default_recipe, "reasoning_budget_message"
        ),
        "chat_template_file": _source_simple(
            ovr, recipe, default_recipe, "chat_template_file"
        ),
        "sampling": _source_recipe_only(ovr, recipe, "sampling"),
        "chat_template_kwargs": _source_recipe_only(ovr, recipe, "chat_template_kwargs"),
    }

    # --- rendered cmd (for the "Raw cmd" expander) ---
    cmd = build_cmd(
        recipe,
        files,
        default_recipe=default_recipe,
        binary_override=binary_override,
        options=options,
        overrides=overrides,
    )

    return EvaluatedConfig(
        name=recipe.name,
        entry_id=recipe.name,
        matched_recipe=recipe.name,
        binary=resolved_binary,
        files=files,
        kv_cache=kv_cache,
        mmproj_offload=mmproj_offload,
        spec=spec,
        ctx_min=ctx_min,
        parallel=parallel,
        reasoning_budget=reasoning_budget,
        reasoning_budget_message=reasoning_budget_message,
        chat_template_file=chat_template_file,
        sampling=sampling,
        chat_template_kwargs=chat_template_kwargs,
        provenance=provenance,
        cmd=cmd,
    )


def evaluate_all(
    catalog: Catalog,
    recipes: Recipes,
    overrides: dict[str, dict[str, Any]] | None,
    *,
    binary_override: str | None = None,
    options: BuildOptions,
) -> dict[str, EvaluatedConfig]:
    """Evaluate every LLM catalog entry. Returns ``{entry_id: EvaluatedConfig}``."""
    ovr = overrides or {}
    out: dict[str, EvaluatedConfig] = {}
    all_ids: set[str] = set()

    for source_key, entries_for_source in catalog.by_source().items():
        for entry in entries_for_source:
            resolved = recipes.resolve(entry.name)
            if not resolved.matched:
                continue
            multi = len(resolved.matched) > 1
            files = detect_files(entry)

            for recipe in resolved.matched:
                entry_id = make_entry_id(
                    entry.name,
                    recipe,
                    multi_match=multi,
                    all_ids=all_ids,
                    source=source_key,
                )
                display = make_display_name(entry.name, recipe, multi)
                evaluated = evaluate_recipe(
                    recipe,
                    files,
                    default_recipe=recipes.default,
                    binary_override=binary_override,
                    options=options,
                    overrides=ovr.get(entry_id),
                )
                # Patch in catalog-derived fields that evaluate_recipe
                # doesn't know about.
                out[entry_id] = EvaluatedConfig(
                    name=display,
                    entry_id=entry_id,
                    matched_recipe=recipe.name,
                    binary=evaluated.binary,
                    files=evaluated.files,
                    kv_cache=evaluated.kv_cache,
                    mmproj_offload=evaluated.mmproj_offload,
                    spec=evaluated.spec,
                    ctx_min=evaluated.ctx_min,
                    parallel=evaluated.parallel,
                    reasoning_budget=evaluated.reasoning_budget,
                    reasoning_budget_message=evaluated.reasoning_budget_message,
                    chat_template_file=evaluated.chat_template_file,
                    sampling=evaluated.sampling,
                    chat_template_kwargs=evaluated.chat_template_kwargs,
                    provenance=evaluated.provenance,
                    cmd=evaluated.cmd,
                )
    return out


__all__ = [
    "EvaluatedConfig",
    "FieldSource",
    "evaluate_all",
    "evaluate_recipe",
]