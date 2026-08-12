"""The plugin ships its own recipes; the repo-root copy still feeds bin/ (ADR-008)."""

from __future__ import annotations

from genesis_worker.services.llama_swap.recipes import BUNDLED_RECIPES_PATH, Recipes


def test_bundled_recipes_ship_with_the_plugin() -> None:
    assert BUNDLED_RECIPES_PATH.is_file()
    recipes = Recipes.load(BUNDLED_RECIPES_PATH)
    assert recipes.default is not None
    assert recipes.matchable
