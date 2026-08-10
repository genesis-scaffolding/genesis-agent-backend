"""Catalog schema (pydantic).

The :class:`Catalog` is the unified output of :class:`CatalogService` —
one entry per model discovered by any registered source. Pydantic gives
us shape validation; PyYAML emission happens at the writer layer
(ADR-006).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import ModelPiece


class ModelEntry(BaseModel):
    """One model in the catalog."""

    name: str
    source: str
    pieces: list[ModelPiece] = Field(default_factory=list)
    total_bytes: int
    directory: str
    notes: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class Catalog(BaseModel):
    """The unified catalog. ``huggingface`` and ``lmstudio`` lists are kept
    separate so the YAML output matches the existing artifact shape."""

    root: str
    generated_at: str
    huggingface: list[ModelEntry] = Field(default_factory=list)
    lmstudio: list[ModelEntry] = Field(default_factory=list)


__all__ = ["Catalog", "ModelEntry"]
