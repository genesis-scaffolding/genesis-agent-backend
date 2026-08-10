"""llama-swap inference service implementation.

This package contains the llama-swap-specific code: the service class
itself, its config generator, its lifecycle (tmux + curl), its agent
export, and the recipe / override storage it depends on.

Submodules:
    service        LlamaSwapService (InferenceService implementation)
    recipes        pydantic schema + longest-match resolver
    overrides      per-model user overrides (overrides.yaml)
    config         config.yaml emission from catalog + recipes + overrides
    lifecycle      tmux + curl (plan-002)
    agent_export   pi-models.json emission (plan-002)
"""

from .overrides import OverridesStore
from .recipes import Recipe, Recipes, ResolvedRecipes
from .service import LlamaSwapService

__all__ = [
    "LlamaSwapService",
    "OverridesStore",
    "Recipe",
    "Recipes",
    "ResolvedRecipes",
]
