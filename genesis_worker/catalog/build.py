"""Catalog build service.

Iterates every registered source, asks each for its discovered models,
merges them into one :class:`Catalog`. PyYAML emission (ADR-006) lives in
the writer layer at :mod:`genesis_worker.catalog.emit`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..sources._base import DiscoveredModel
from ..sources._registry import all_sources
from .schema import Catalog, ModelEntry


class CatalogService:
    """Walks the vault and produces a unified catalog."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def rescan(self) -> Catalog:
        """Re-walk every source and merge results."""
        discovered: list[DiscoveredModel] = []
        for source in all_sources():
            source_local = _override_source_local_path(source, self.vault_path)
            if source_local.is_available():
                discovered.extend(source_local.walk())
        return _build_catalog(discovered, root=str(self.vault_path))


def _override_source_local_path(source, vault_path: Path):
    """Temporarily point a source at a specific vault path.

    The source classes' ``local_path()`` defaults are XDG dirs; this
    helper sets ``_local_path`` so the source walks the vault the user
    actually uses. The source's ``is_available()`` already checks
    ``local_path()``, so this just routes to the right place.
    """
    if source.name == "huggingface":
        source._local_path = vault_path / "huggingface" / "hub"
    elif source.name == "lmstudio":
        source._local_path = vault_path / "lmstudio" / "models"
    return source


def _build_catalog(discovered: list[DiscoveredModel], *, root: str) -> Catalog:
    by_source: dict[str, list[ModelEntry]] = {"huggingface": [], "lmstudio": []}
    for d in discovered:
        entry = ModelEntry(
            name=d.native_id,
            source=d.source,
            pieces=list(d.pieces),
            total_bytes=d.total_bytes,
            directory=str(d.directory),
            notes=list(d.notes),
            extra=dict(d.extra),
        )
        by_source.setdefault(d.source, []).append(entry)
    return Catalog(
        root=root,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        huggingface=by_source.get("huggingface", []),
        lmstudio=by_source.get("lmstudio", []),
    )


__all__ = ["CatalogService"]
