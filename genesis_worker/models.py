"""Framework-level entity types.

This module is the central home for the worker's dataclasses — every
type that flows between modules or is returned to consumers (CLI,
Streamlit, tests) lives here. The goal is a single, well-known place
to find framework-level data shapes, rather than scattering them
across `facade.py`, `services/`, `catalog/`, etc.

What's in here:

- **Discovery entities** (``ModelPiece``, ``DiscoveredModel``) — produced
  by ``ModelSource`` walkers, consumed by the catalog.
- **Extension info** (``SourceInfo``, ``ServiceInfo``) — produced by the
  facade, consumed by UI / CLI listings.

Each type's domain is documented in its own docstring; the role of
*this* file is to be the single import location for framework-level
data shapes.

Pydantic schemas (settings, catalog, recipes) live in their own
domain files (``settings.py``, ``catalog/schema.py``,
``services/llama_swap/recipes.py``) because they each carry their own
validation and serialization concerns. They are *not* scattered —
each is co-located with the module that produces or consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .services._base import ServiceCapabilities

# ---------------------------------------------------------------------------
# Discovery entities (source -> catalog)
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


__all__ = [
    "DiscoveredModel",
    "ModelPiece",
    "ServiceInfo",
    "SourceInfo",
]
