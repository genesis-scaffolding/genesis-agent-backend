"""Recipes: pydantic schema, loader, longest-match resolver."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class Recipe(BaseModel):
    """One recipe entry, plus the recipe's name as a field."""

    name: str
    match: str | None = None
    binary: str | None = None
    sampling: dict[str, Any] = Field(default_factory=dict)
    chat_template_file: str | None = None
    chat_template_kwargs: dict[str, Any] = Field(default_factory=dict)
    parallel: int | None = None
    spec: dict[str, Any] | None = None
    kv_cache: str | None = None
    mmproj_offload: bool | None = None
    ctx_min: int | None = None
    reasoning_budget: int | None = None
    reasoning_budget_message: str | None = None


class Recipes(BaseModel):
    """The full recipes.yaml: a default recipe plus matchable recipes."""

    default: Recipe | None = None
    matchable: list[Recipe] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        """Allow constructing from a dict-of-dicts as well as a model."""
        return data

    @classmethod
    def load(cls, path: Path) -> Recipes:
        """Load recipes.yaml and split into default + matchable."""
        raw = yaml.safe_load(path.read_text())
        rec_dict = (raw or {}).get("recipes", {})
        default = None
        matchable: list[Recipe] = []
        for name, body in rec_dict.items():
            r = Recipe(name=name, **(body or {}))
            if r.match is None or not str(r.match).strip():
                default = r
            else:
                matchable.append(r)
        return cls(default=default, matchable=matchable)

    def resolve(self, model_name: str) -> ResolvedRecipes:
        """Return which recipes match this model and which keyword won."""
        base = model_name.split("/", 1)[-1] if "/" in model_name else model_name
        norm_model = _normalize(base)

        matched: list[tuple[Recipe, str]] = []
        for recipe in self.matchable:
            kw_field = (recipe.match or "").strip()
            if not kw_field:
                continue
            kw = _normalize(kw_field)
            if kw and kw in norm_model:
                matched.append((recipe, kw))

        if not matched:
            if self.default is None:
                return ResolvedRecipes(matched=[], winner_keyword="", winner_recipe=None)
            return ResolvedRecipes(
                matched=[self.default],
                winner_keyword="default",
                winner_recipe=self.default,
            )

        # Substring shadowing: keep only the longest keyword(s).
        keywords = sorted({kw for _, kw in matched}, key=len, reverse=True)
        kept: set[str] = set()
        for kw in keywords:
            if not any(kw in kept_kw for kept_kw in kept):
                kept.add(kw)
        # winner_keyword is the longest kept; winner_recipe is the first
        # recipe whose keyword equals the winner.
        winner_kw = next(iter(kept))
        winner_recipe = next(r for r, kw in matched if kw == winner_kw)

        return ResolvedRecipes(
            matched=[r for r, kw in matched if kw in kept],
            winner_keyword=winner_kw,
            winner_recipe=winner_recipe,
        )


@dataclass(frozen=True)
class ResolvedRecipes:
    """Resolver output: which recipes matched, and which keyword won.

    ``winner_recipe`` is the recipe whose cascade should be applied for
    fields not overridden by the user. The Config Editor (spec-003)
    uses ``winner_keyword`` to render "from recipe: <name>" badges in
    the UI.
    """

    matched: list[Recipe]
    winner_keyword: str
    winner_recipe: Recipe | None


def _normalize(s: str) -> str:
    """Lowercase + strip hyphens / underscores / dots.

    Stripping dots lets ``qwen3.6`` match ``Qwen3.6-35B-A3B`` after
    normalization; qwen3.5 and qwen3.6 still stay distinct because
    the digits differ.
    """
    return s.lower().replace("-", "").replace("_", "").replace(".", "")


__all__ = ["Recipe", "Recipes", "ResolvedRecipes"]
