"""llama-swap inference service implementation.

This package is self-contained: the service class, its config generator,
its lifecycle (tmux + curl), its agent export, and the recipe /
override storage it depends on. Nothing in this package reaches back
into the framework core.

Submodules:
    recipes        pydantic schema + longest-match resolver
    overrides      per-model user overrides (overrides.yaml)
    config         config.yaml emission from catalog + recipes + overrides
    lifecycle      tmux + curl (plan-002)
    agent_export   pi-models.json emission (plan-002)
    service        LlamaSwapService (plan-002)
"""

from .overrides import OverridesStore
from .recipes import Recipe, Recipes, ResolvedRecipes

__all__ = ["OverridesStore", "Recipe", "Recipes", "ResolvedRecipes"]
