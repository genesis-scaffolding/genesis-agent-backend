"""Catalog build service.

Rescans every registered :class:`ModelSource`, merges their discoveries
into a single :class:`~genesis_worker.models.Catalog`, and caches the
result. The schema types (``Catalog``, ``ModelEntry``) live at the
framework level in :mod:`genesis_worker.models`; this module owns the
service that produces them.

PyYAML emission (ADR-006) lives downstream in
:mod:`genesis_worker.services.llama_swap.config`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import Catalog, DiscoveredModel, ModelEntry
from .sources._registry import SourceRegistry


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
