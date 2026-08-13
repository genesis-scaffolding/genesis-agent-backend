"""Catalog build service."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from .contracts import Catalog, DiscoveredModel, ModelEntry
from .registries import SourceRegistry


class CatalogService:
    """Walks the vault and produces a unified catalog.

    The :class:`SourceRegistry` owns path resolution for every
    registered source; this service just walks them in order. The
    catalog's ``root`` is the registry's ``vault_path``.
    """

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry = registry

    @property
    def vault_path(self):
        return self._registry.vault_path

    def rescan(self) -> Catalog:
        """Re-walk every source and merge results."""
        discovered: list[DiscoveredModel] = []
        for source in self._registry.all():
            if source.is_available():
                discovered.extend(source.walk())
        return _build_catalog(discovered, root=str(self._registry.vault_path))


def _build_catalog(discovered: list[DiscoveredModel], *, root: str) -> Catalog:
    entries = [
        ModelEntry(
            name=d.native_id,
            source=d.source,
            pieces=list(d.pieces),
            total_bytes=d.total_bytes,
            directory=str(d.directory),
            notes=list(d.notes),
            extra=dict(d.extra),
        )
        for d in discovered
    ]
    return Catalog(
        root=root,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        content_hash=compute_content_hash(entries),
        entries=entries,
    )


def compute_content_hash(entries: list[ModelEntry]) -> str:
    """Deterministic sha256-hex over the catalog's content-bearing fields.

    Excludes ``directory``, ``notes``, and ``extra`` because those don't
    affect what llama-swap would generate. Includes piece-level
    ``role`` / ``filename`` / ``bytes`` so file additions and deletions
    flip the hash.
    """
    norm = []
    for e in sorted(entries, key=lambda x: (x.source, x.name)):
        pieces = sorted(
            ((p.role, p.filename, p.bytes) for p in e.pieces),
            key=lambda t: (t[1], t[0], t[2]),
        )
        norm.append([e.source, e.name, e.total_bytes, pieces])
    blob = json.dumps(norm, sort_keys=False, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


__all__ = ["CatalogService", "compute_content_hash"]
