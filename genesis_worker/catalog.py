"""Catalog build service."""

from __future__ import annotations

from pathlib import Path

from .contracts import Catalog, DiscoveredModel
from .registries import SourceRegistry
from .utils.catalog_io import load_catalog, save_catalog
from .utils.catalog_utils import build_catalog


class CatalogService:
    """Walks the vault and produces a unified catalog.

    The :class:`SourceRegistry` owns path resolution for every
    registered source; this service just walks them in order. The
    catalog's ``root`` is the registry's ``vault_path``.

    When ``catalog_path`` is supplied, the service persists the catalog
    to that path and reuses the persisted ``generated_at`` whenever the
    content hash matches what is on disk (ADR-011). When omitted, the
    service behaves as before: fresh ``generated_at`` every rescan.
    """

    def __init__(self, registry: SourceRegistry, catalog_path: Path | None = None) -> None:
        self._registry = registry
        self._catalog_path = catalog_path

    @property
    def vault_path(self):
        return self._registry.vault_path

    def rescan(self) -> Catalog:
        """Re-walk every source and merge results.

        If a ``catalog_path`` is configured, the catalog is persisted and the
        ``generated_at`` is reused across rescans that observe no change to
        the vault. Without a path, a fresh ``generated_at`` is produced every
        call (legacy behaviour).
        """
        discovered: list[DiscoveredModel] = []
        for source in self._registry.all():
            if source.is_available():
                discovered.extend(source.walk())
        new = build_catalog(discovered, root=str(self._registry.vault_path))

        if self._catalog_path is None:
            return new

        previous = load_catalog(self._catalog_path)
        if previous is not None and previous.content_hash == new.content_hash:
            # Content unchanged — return a Catalog with the persisted
            # generated_at. Don't rewrite the file.
            return Catalog(
                schema_version=new.schema_version,
                root=new.root,
                generated_at=previous.generated_at,
                content_hash=new.content_hash,
                entries=new.entries,
            )

        save_catalog(self._catalog_path, new)
        return new


__all__ = ["CatalogService"]
