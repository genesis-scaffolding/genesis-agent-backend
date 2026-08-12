"""Tests for recipe_eval — structured view of recipe + files + overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.contracts.catalog import Catalog, ModelEntry
from genesis_worker.services.llama_swap.generate_config import (
    BuildOptions,
    detect_files,
)
from genesis_worker.services.llama_swap.recipe_eval import (
    FieldSource,
    evaluate_all,
    evaluate_recipe,
)
from genesis_worker.services.llama_swap.recipes import (
    BUNDLED_RECIPES_PATH,
    Recipe,
    Recipes,
    RecipesStore,
)


@pytest.fixture
def options(tmp_path: Path) -> BuildOptions:
    return BuildOptions(repo_root=tmp_path)


def _recipe(
    *,
    name: str = "test",
    binary: str | None = None,
    sampling: dict | None = None,
    chat_template_file: str | None = None,
    chat_template_kwargs: dict | None = None,
    parallel: int | None = None,
    spec: dict | None = None,
    kv_cache: str | None = None,
    mmproj_offload: bool | None = None,
    ctx_min: int | None = None,
    reasoning_budget: int | None = None,
    reasoning_budget_message: str | None = None,
) -> Recipe:
    return Recipe(
        name=name,
        binary=binary,
        sampling=sampling if sampling is not None else {},
        chat_template_file=chat_template_file,
        chat_template_kwargs=chat_template_kwargs if chat_template_kwargs is not None else {},
        parallel=parallel,
        spec=spec,
        kv_cache=kv_cache,
        mmproj_offload=mmproj_offload,
        ctx_min=ctx_min,
        reasoning_budget=reasoning_budget,
        reasoning_budget_message=reasoning_budget_message,
    )


# ---------------------------------------------------------------------------
# evaluate_recipe — basic fields
# ---------------------------------------------------------------------------


def test_evaluate_recipe_returns_none_for_unset_fields(options: BuildOptions) -> None:
    recipe = _recipe()
    files = detect_files(_entry())
    evaluated = evaluate_recipe(recipe, files, options=options)
    assert evaluated.parallel is None
    assert evaluated.kv_cache is None
    assert evaluated.ctx_min is None
    assert evaluated.sampling == {}


def test_evaluate_recipe_uses_recipe_values(options: BuildOptions) -> None:
    recipe = _recipe(parallel=2, kv_cache="q8_0", ctx_min=131072)
    evaluated = evaluate_recipe(recipe, detect_files(_entry()), options=options)
    assert evaluated.parallel == 2
    assert evaluated.kv_cache == "q8_0"
    assert evaluated.ctx_min == 131072


def test_evaluate_recipe_override_wins(options: BuildOptions) -> None:
    recipe = _recipe(parallel=2, kv_cache="q8_0")
    evaluated = evaluate_recipe(
        recipe,
        detect_files(_entry()),
        options=options,
        overrides={"parallel": 5, "kv_cache": "q4_0"},
    )
    assert evaluated.parallel == 5
    assert evaluated.kv_cache == "q4_0"


def test_evaluate_recipe_falls_back_to_default_recipe(options: BuildOptions) -> None:
    recipe = _recipe()  # no parallel
    default = _recipe(parallel=4)
    evaluated = evaluate_recipe(
        recipe,
        detect_files(_entry()),
        default_recipe=default,
        options=options,
    )
    assert evaluated.parallel == 4
    assert evaluated.provenance["parallel"] == FieldSource.DEFAULT


def test_evaluate_recipe_override_blocks_default_fallback(options: BuildOptions) -> None:
    recipe = _recipe()
    default = _recipe(parallel=4)
    evaluated = evaluate_recipe(
        recipe,
        detect_files(_entry()),
        default_recipe=default,
        options=options,
        overrides={"parallel": 7},
    )
    assert evaluated.parallel == 7
    assert evaluated.provenance["parallel"] == FieldSource.OVERRIDE


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_marks_override(options: BuildOptions) -> None:
    recipe = _recipe(parallel=1)
    evaluated = evaluate_recipe(
        recipe,
        detect_files(_entry()),
        options=options,
        overrides={"parallel": 9},
    )
    assert evaluated.provenance["parallel"] == FieldSource.OVERRIDE


def test_provenance_marks_recipe(options: BuildOptions) -> None:
    recipe = _recipe(parallel=1)
    evaluated = evaluate_recipe(recipe, detect_files(_entry()), options=options)
    assert evaluated.provenance["parallel"] == FieldSource.RECIPE


def test_provenance_marks_default(options: BuildOptions) -> None:
    recipe = _recipe()
    default = _recipe(parallel=1)
    evaluated = evaluate_recipe(
        recipe,
        detect_files(_entry()),
        default_recipe=default,
        options=options,
    )
    assert evaluated.provenance["parallel"] == FieldSource.DEFAULT


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def test_binary_from_override(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = evaluate_recipe(
        recipe,
        detect_files(_entry()),
        options=options,
        overrides={"binary": "/custom/path/server"},
    )
    assert evaluated.binary == "/custom/path/server"
    assert evaluated.provenance["binary"] == FieldSource.OVERRIDE


def test_binary_falls_back_to_default_rel(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = evaluate_recipe(recipe, detect_files(_entry()), options=options)
    # default_binary_rel is relative; resolved against repo_root.
    assert evaluated.binary == str((options.repo_root / "vendor/llama.cpp/build/bin/llama-server").resolve())
    assert evaluated.provenance["binary"] == FieldSource.COMPUTED


# ---------------------------------------------------------------------------
# Sampling / chat_template_kwargs (no default-recipe fallback)
# ---------------------------------------------------------------------------


def test_sampling_uses_recipe_dict(options: BuildOptions) -> None:
    recipe = _recipe(sampling={"temp": 0.7, "top_p": 0.9})
    evaluated = evaluate_recipe(recipe, detect_files(_entry()), options=options)
    assert evaluated.sampling == {"temp": 0.7, "top_p": 0.9}
    assert evaluated.provenance["sampling"] == FieldSource.RECIPE


def test_sampling_override_wins(options: BuildOptions) -> None:
    recipe = _recipe(sampling={"temp": 0.7})
    evaluated = evaluate_recipe(
        recipe,
        detect_files(_entry()),
        options=options,
        overrides={"sampling": {"temp": 0.3}},
    )
    assert evaluated.sampling == {"temp": 0.3}
    assert evaluated.provenance["sampling"] == FieldSource.OVERRIDE


def test_chat_template_kwargs_empty_when_unset(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = evaluate_recipe(recipe, detect_files(_entry()), options=options)
    assert evaluated.chat_template_kwargs is None or evaluated.chat_template_kwargs == {}


# ---------------------------------------------------------------------------
# evaluate_all — catalog walk
# ---------------------------------------------------------------------------


def test_evaluate_all_walks_catalog(tmp_path: Path) -> None:
    catalog = _small_catalog()
    recipes = RecipesStore(BUNDLED_RECIPES_PATH).load()
    out = evaluate_all(catalog, recipes, overrides={}, options=BuildOptions(repo_root=tmp_path))
    assert out, "expected at least one evaluated entry"


def test_evaluate_all_returns_empty_when_no_match(tmp_path: Path) -> None:
    catalog = _small_catalog()
    # Recipes with no matches.
    recipes = Recipes(default=None, matchable=[])
    out = evaluate_all(catalog, recipes, overrides={}, options=BuildOptions(repo_root=tmp_path))
    assert out == {}


def test_evaluate_all_includes_overrides(tmp_path: Path) -> None:
    catalog = _small_catalog()
    recipes = RecipesStore(BUNDLED_RECIPES_PATH).load()
    entries = list(out.values()) if (out := evaluate_all(
        catalog, recipes, overrides={}, options=BuildOptions(repo_root=tmp_path)
    )) else []
    assert entries, "no evaluated entries to test"
    entry_id = entries[0].entry_id
    overrides = {entry_id: {"parallel": 99}}
    out2 = evaluate_all(
        catalog, recipes, overrides=overrides, options=BuildOptions(repo_root=tmp_path)
    )
    assert out2[entry_id].parallel == 99
    assert out2[entry_id].provenance["parallel"] == FieldSource.OVERRIDE


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _entry() -> ModelEntry:
    return ModelEntry(
        name="test/foo",
        source="huggingface",
        pieces=[],
        total_bytes=0,
        directory="/tmp/vault/test/foo",
        notes=[],
        extra={},
    )


def _small_catalog() -> Catalog:
    return Catalog(
        root="/tmp/vault",
        generated_at="2026-01-01T00:00:00+00:00",
        huggingface=[
            ModelEntry(
                name="test/qwen3-gguf",
                source="huggingface",
                pieces=[],
                total_bytes=0,
                directory="/tmp/vault/test/qwen3-gguf",
                notes=[],
                extra={},
            )
        ],
        lmstudio=[],
    )