"""Model source extension axis.

A :class:`ModelSource` knows how to discover (and, optionally, acquire)
models from one kind of repository: HuggingFace cache, LM Studio layout,
ModelScope, Civitai, etc.

The framework iterates ``all_sources()`` and merges their discoveries
into a unified :class:`~genesis_worker.catalog.schema.Catalog`. Adding a
new source is one new module under ``genesis_worker/sources/`` — see
:mod:`genesis_worker.sources._registry`.

ADR-003 details the extension architecture.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


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


@runtime_checkable
class ModelSource(Protocol):
    """One kind of model repository."""

    name: str
    display_name: str
    can_acquire: bool

    def is_available(self) -> bool: ...
    def local_path(self) -> Path: ...
    def walk(self) -> Sequence[DiscoveredModel]: ...


__all__ = [
    "DiscoveredModel",
    "ModelPiece",
    "ModelSource",
]
