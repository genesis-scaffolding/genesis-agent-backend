"""The plugin ships its own recipes; the repo-root copy still feeds bin/ (ADR-008)."""

from __future__ import annotations

import pytest

from genesis_worker.services.llama_swap.recipes import BUNDLED_RECIPES_PATH, Recipes

REPO_ROOT_RECIPES = BUNDLED_RECIPES_PATH.parents[4] / "recipes.yaml"


def test_bundled_recipes_ship_with_the_plugin() -> None:
    assert BUNDLED_RECIPES_PATH.is_file()
    recipes = Recipes.load(BUNDLED_RECIPES_PATH)
    assert recipes.default is not None
    assert recipes.matchable


def test_bundled_recipes_match_the_live_copy() -> None:
    """Until bin/build-config.py retires, both copies must stay in sync.

    If this fails, the two have drifted — copy recipes.yaml over
    genesis_worker/services/llama_swap/data/recipes.yaml (or the reverse,
    whichever is newer) and re-run.
    """
    if not REPO_ROOT_RECIPES.is_file():
        pytest.skip("repo-root recipes.yaml already retired")
    live = Recipes.load(REPO_ROOT_RECIPES)
    bundled = Recipes.load(BUNDLED_RECIPES_PATH)
    assert bundled.default == live.default
    assert {r.name for r in bundled.matchable} == {r.name for r in live.matchable}
    assert bundled.matchable == live.matchable
