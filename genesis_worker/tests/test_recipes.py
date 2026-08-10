"""Tests for recipes schema, loader, and longest-match resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genesis_worker.services.llama_swap.recipes import Recipe, Recipes


@pytest.fixture
def recipes_yaml(tmp_path: Path) -> Path:
    """A representative recipes.yaml covering the shadowing + sibling cases."""
    body = {
        "recipes": {
            "default": {
                "binary": "vendor/llama.cpp/build/bin/llama-server",
                "ctx_min": 131072,
                "parallel": 1,
                "sampling": {"temp": 0.8, "top_p": 0.95, "top_k": 40},
            },
            "qwen3.6-thinking": {
                "match": "qwen3.6",
                "sampling": {"temp": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0},
                "chat_template_kwargs": {"preserve_thinking": True},
                "spec": {"type": "draft-mtp", "n_max": 2},
                "reasoning_budget": 12288,
            },
            "qwen3.6-instruct": {
                "match": "qwen3.6",
                "sampling": {"temp": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
                "chat_template_kwargs": {"enable_thinking": False},
                "spec": {"type": "draft-mtp", "n_max": 2, "p_min": 0.75},
                "reasoning_budget": 0,
            },
            "qwen3.6-27b-thinking": {
                "match": "qwen3.6-27b",
                "sampling": {"temp": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0},
                "kv_cache": "q8_0",
                "mmproj_offload": True,
            },
            "bonsai": {
                "match": "bonsai",
                "binary": "vendor/prism-llama.cpp/build/bin/llama-server",
                "sampling": {"temp": 0.7, "top_p": 0.95, "top_k": 20},
                "kv_cache": "q8_0",
                "ctx_min": 150000,
            },
            "lfm2": {
                "match": "lfm2",
                "sampling": {"temp": 0.1, "top_k": 50, "repeat_penalty": 1.1},
                "ctx_min": 131072,
            },
        },
    }
    path = tmp_path / "recipes.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_load_splits_default_and_matchable(recipes_yaml: Path) -> None:
    r = Recipes.load(recipes_yaml)
    assert r.default is not None
    assert r.default.name == "default"
    assert {m.name for m in r.matchable} == {
        "qwen3.6-thinking",
        "qwen3.6-instruct",
        "qwen3.6-27b-thinking",
        "bonsai",
        "lfm2",
    }


def test_resolve_qwen36_35b_emits_both_siblings(recipes_yaml: Path) -> None:
    r = Recipes.load(recipes_yaml)
    res = r.resolve("unsloth/Qwen3.6-35B-A3B-GGUF")
    assert {m.name for m in res.matched} == {"qwen3.6-thinking", "qwen3.6-instruct"}
    assert res.winner_keyword == "qwen36"


def test_resolve_qwen36_27b_shadows_qwen36(recipes_yaml: Path) -> None:
    """A more specific match wins; siblings within that scope still apply."""
    r = Recipes.load(recipes_yaml)
    res = r.resolve("unsloth/Qwen3.6-27B-MTP-GGUF")
    # qwen3.6-27b-thinking matches "qwen3.6-27b" which is a substring of "qwen3.6".
    # substring shadowing: qwen36 is shadowed by qwen3627b, so only qwen3.6-27b-thinking matches.
    assert {m.name for m in res.matched} == {"qwen3.6-27b-thinking"}
    assert res.winner_keyword == "qwen3627b"


def test_resolve_bonsai_uses_bonsai_recipe(recipes_yaml: Path) -> None:
    r = Recipes.load(recipes_yaml)
    res = r.resolve("prism-ml/Ternary-Bonsai-27B-gguf")
    assert res.winner_keyword == "bonsai"
    assert res.winner_recipe is not None
    assert res.winner_recipe.name == "bonsai"
    assert res.winner_recipe.binary == "vendor/prism-llama.cpp/build/bin/llama-server"


def test_resolve_unknown_falls_back_to_default(recipes_yaml: Path) -> None:
    r = Recipes.load(recipes_yaml)
    res = r.resolve("acme/rocinante-16b")
    assert res.winner_keyword == "default"
    assert res.winner_recipe is not None
    assert res.winner_recipe.name == "default"


def test_resolve_handles_dots_and_underscores(recipes_yaml: Path) -> None:
    """Normalization strips dots/underscores/hyphens so qwen3.6 still matches Qwen-3.6."""
    r = Recipes.load(recipes_yaml)
    res = r.resolve("acme/Qwen_3-6_22B-GGUF")
    assert res.winner_keyword == "qwen36"


def test_resolve_uses_default_when_no_recipes_match(tmp_path: Path) -> None:
    path = tmp_path / "recipes.yaml"
    path.write_text(yaml.safe_dump({"recipes": {"default": {"ctx_min": 8192}}}))
    r = Recipes.load(path)
    res = r.resolve("anything")
    assert res.winner_keyword == "default"


def test_recipe_ignores_unknown_fields() -> None:
    """Unknown YAML keys in a recipe are silently dropped (forward compat).

    A future recipes.yaml may add fields the current schema doesn't know
    about; we want loading to succeed so older workers can still parse
    newer recipes.
    """
    from pydantic import ValidationError

    # Recipe() doesn't accept unknown kwargs at construction (pydantic is
    # strict on __init__). Use model_validate to test the YAML-loading path.
    try:
        Recipe.model_validate({"name": "x", "match": "x", "future_field": "ok"})
    except ValidationError:
        pytest.fail("model_validate should accept unknown fields, not raise")


def test_load_real_repo_recipes() -> None:
    """The real recipes.yaml in this repo loads cleanly and has at least one matchable recipe."""
    r = Recipes.load(Path("recipes.yaml"))
    assert r.default is not None
    assert len(r.matchable) > 0
