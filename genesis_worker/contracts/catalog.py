"""Catalog entity types shared by the framework and its plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ModelPiece:
    """One file in a model directory."""

    role: str  # "main", "mmproj", "mtp", "transformer", "vae", "config"
    filename: str
    path: Path
    bytes: int


@dataclass(frozen=True)
class DiscoveredModel:
    """One model as discovered by a source."""

    source: str
    native_id: str  # "org/repo" or "publisher/model-dir"
    pieces: list[ModelPiece]
    total_bytes: int
    directory: Path
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class ModelEntry(BaseModel):
    """One model in the unified catalog — the YAML-facing form of a DiscoveredModel."""

    name: str
    source: str
    pieces: list[ModelPiece] = Field(default_factory=list)
    total_bytes: int
    directory: str
    notes: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class Catalog(BaseModel):
    """The unified catalog.

    ``huggingface`` and ``lmstudio`` are named fields so the YAML output keeps the
    shape ``bin/catalog.py`` produces (ADR-008). ADR-009 records this as a known
    boundary exception; ``by_source()`` is how consumers avoid depending on it.
    """

    root: str
    generated_at: str
    huggingface: list[ModelEntry] = Field(default_factory=list)
    lmstudio: list[ModelEntry] = Field(default_factory=list)

    def by_source(self) -> dict[str, list[ModelEntry]]:
        result: dict[str, list[ModelEntry]] = {}
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if isinstance(value, list) and all(isinstance(v, ModelEntry) for v in value):
                result[field_name] = value
        return result


__all__ = [
    "Catalog",
    "DiscoveredModel",
    "ModelEntry",
    "ModelPiece",
]
