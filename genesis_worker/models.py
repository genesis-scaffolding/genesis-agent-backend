"""Framework-level entity types — the single home for the worker's data shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .services._base import ServiceCapabilities


# ---------------------------------------------------------------------------
# Discovery entities (source -> catalog build)
# ---------------------------------------------------------------------------


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

    source: str  # "huggingface", "lmstudio"
    native_id: str  # "org/repo" or "publisher/model-dir"
    pieces: list[ModelPiece]
    total_bytes: int
    directory: Path
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Extension info (facade -> UI / CLI)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceInfo:
    """Display-oriented view of one registered source.

    Returned by :meth:`GenesisWorker.list_sources` for UI / CLI listings.
    Captures the static metadata (``name``, ``display_name``,
    ``can_acquire``) plus a live ``is_available`` snapshot so consumers
    don't need to call ``is_available()`` on each source themselves.
    """

    name: str
    display_name: str
    can_acquire: bool
    is_available: bool


@dataclass(frozen=True)
class ServiceInfo:
    """Display-oriented view of one registered service.

    Returned by :meth:`GenesisWorker.list_services` for UI / CLI listings.
    Captures the static metadata (``name``, ``display_name``) plus a
    snapshot of the service's :class:`ServiceCapabilities`.
    """

    name: str
    display_name: str
    capabilities: ServiceCapabilities


# ---------------------------------------------------------------------------
# Catalog schemas (pydantic — the YAML-facing representation)
# ---------------------------------------------------------------------------


class ModelEntry(BaseModel):
    """One model in the unified catalog.

    This is the YAML-facing representation; ``DiscoveredModel`` is the
    in-memory representation produced by walkers. The two are kept
    separate so that walker output stays a simple dataclass while the
    catalog gets pydantic validation and a stable serialization shape.
    """

    name: str
    source: str
    pieces: list[ModelPiece] = Field(default_factory=list)
    total_bytes: int
    directory: str
    notes: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class Catalog(BaseModel):
    """The unified catalog. ``huggingface`` and ``lmstudio`` lists are kept
    separate so the YAML output matches the existing artifact shape (ADR-008)."""

    root: str
    generated_at: str
    huggingface: list[ModelEntry] = Field(default_factory=list)
    lmstudio: list[ModelEntry] = Field(default_factory=list)

    def by_source(self) -> dict[str, list[ModelEntry]]:
        """Return entries grouped by source name.

        Source-agnostic iteration over the catalog. Walks the model's
        declared fields and returns any field whose value is a list of
        ``ModelEntry`` (works for empty lists too).

        Adding a new source field (e.g. ``modelscope: list[ModelEntry]``)
        automatically appears in this mapping — no second edit in
        downstream consumers needed.
        """
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
    "ServiceInfo",
    "SourceInfo",
]
