"""Recipes: pydantic schema, loader, longest-match resolver.

Loaded from two sources, in order: the bundled ``data/recipes.yaml`` and an
optional user overlay beside the generated config. Each recipe carries its
``source`` (which file it came from) so provenance rides the resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

BUNDLED_RECIPES_PATH = Path(__file__).parent / "data" / "recipes.yaml"


class RecipesStore:
    """Lazily loads and caches merged :class:`Recipes` from ordered paths.

    ``paths[0]`` is the base (bundled) doc; each subsequent path is an
    overlay applied in order. Missing overlays are skipped; a missing base
    file or a present file that fails YAML/schema parsing is a hard error.
    """

    def __init__(self, paths: list[Path]) -> None:
        self.paths = tuple(paths)
        self._cached: Recipes | None = None

    def load(self) -> Recipes:
        if self._cached is None:
            self._cached = self._load_all()
        return self._cached

    def _load_all(self) -> Recipes:
        merged = self._load_doc(self.paths[0], "bundled")
        for path in self.paths[1:]:
            if not path.exists():
                continue
            merged = merge_recipes(merged, self._load_doc(path, "override"))
        return merged

    @staticmethod
    def _load_doc(path: Path, source: str) -> Recipes:
        try:
            return Recipes.load(path, source=source)
        except (yaml.YAMLError, ValidationError, OSError) as exc:
            raise RuntimeError(f"recipes file {path}: {exc}") from exc

    def reload(self) -> Recipes:
        self._cached = None
        return self.load()


class Recipe(BaseModel):
    """One recipe entry, plus the recipe's name as a field."""

    name: str
    source: str = "bundled"  # "bundled" | "override" — set at load/merge time
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
    def load(cls, path: Path, source: str = "bundled") -> Recipes:
        """Load recipes.yaml and split into default + matchable."""
        raw = yaml.safe_load(path.read_text())
        rec_dict = (raw or {}).get("recipes", {})
        default = None
        matchable: list[Recipe] = []
        for name, body in rec_dict.items():
            body = dict(body or {})
            body.pop("source", None)  # source is stamped, never user-set
            r = Recipe(name=name, source=source, **body)
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


def merge_recipes(base: Recipes, overlay: Recipes) -> Recipes:
    """Merge two recipe sets, recipe-level: overlay wins on name collision.

    Base order is preserved; overlay recipes with new names are appended in
    document order. The overlay's default replaces the base's.
    """
    by_name: dict[str, Recipe] = {r.name: r for r in base.matchable}
    for recipe in overlay.matchable:
        by_name[recipe.name] = recipe
    base_names = {r.name for r in base.matchable}
    matchable = [by_name[r.name] for r in base.matchable] + [
        r for name, r in by_name.items() if name not in base_names
    ]
    default = overlay.default if overlay.default is not None else base.default
    return Recipes(default=default, matchable=matchable)


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


__all__ = [
    "BUNDLED_RECIPES_PATH",
    "Recipe",
    "Recipes",
    "RecipesStore",
    "ResolvedRecipes",
    "merge_recipes",
]
