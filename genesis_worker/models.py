"""Framework-level schema entities.

These dataclasses are produced by :class:`ModelSource` walkers and
consumed by :class:`Catalog`. They live at the framework level (not
inside :mod:`genesis_worker.sources`) because they are not source-axis
primitives — they're the entity vocabulary that all extension axes
share.

Sources declare ``walk() -> Sequence[DiscoveredModel]``; the catalog
aggregates discoveries into :class:`~genesis_worker.catalog.schema.Catalog`.
A future service axis (e.g. ComfyUI, AIToolkit) that emits discoveries
will use the same dataclasses.

ADR-003 places :class:`ModelSource` and :class:`InferenceService` at
the framework level; these entities live alongside them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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


__all__ = ["DiscoveredModel", "ModelPiece"]
