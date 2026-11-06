"""The plugin ships its own recipes; the repo-root copy still feeds bin/ (ADR-008)."""

from __future__ import annotations

import yaml

from genesis_worker.services.llama_swap.engines import Engine
from genesis_worker.services.llama_swap.recipes import BUNDLED_RECIPES_PATH, Recipes


def test_bundled_recipes_ship_with_the_plugin() -> None:
    assert BUNDLED_RECIPES_PATH.is_file()
    recipes = Recipes.load(BUNDLED_RECIPES_PATH)
    assert recipes.default is not None
    assert recipes.default.engine is None
    assert recipes.matchable
    assert {recipe.engine for recipe in recipes.matchable} == {
        Engine.LLAMA_SERVER,
        Engine.RADIANCE,
    }


def test_tagged_bundled_recipe_dumps_to_yaml() -> None:
    recipes = Recipes.load(BUNDLED_RECIPES_PATH)
    tagged = next(recipe for recipe in recipes.matchable if recipe.engine is not None)

    dumped = yaml.safe_dump(tagged.model_dump(exclude={"source"}))

    engine = tagged.engine
    assert engine is not None
    assert f"engine: {engine.value}" in dumped
