"""GenesisWorker — the single public facade for the worker package.

The :class:`GenesisWorker` is the entry point that CLI scripts,
Streamlit pages, and external consumers (e.g. the orchestrator) use to
drive the worker. It owns:

- :class:`~genesis_worker.settings.Settings` — constructed if not provided.
- :class:`~genesis_worker.sources.SourceRegistry` — auto-discovers sources.
- :class:`~genesis_worker.services.ServiceRegistry` — auto-discovers services.
- :class:`~genesis_worker.catalog.CatalogService` — uses the source registry
  to walk the vault and produce the unified catalog.

Consumers never reach into the registries directly (well, they can via
the ``sources`` / ``services`` properties) — they ask the worker to
list sources, rescan the catalog, look up a service, etc. Adding a new
source or service requires no changes here: the registries auto-discover.

Methods that depend on spec-002 (acquire flows, lifecycle plumbing,
metrics collection) are intentionally absent; they land in plan-002/3.

ADR-003 details the facade rationale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog.build import CatalogService
from .catalog.schema import Catalog
from .models import ServiceInfo, SourceInfo
from .services._registry import ServiceRegistry
from .sources._registry import SourceRegistry

if TYPE_CHECKING:
    from .settings import Settings


class GenesisWorker:
    """Top-level facade for the worker.

    Construction wires together the four building blocks: settings,
    source registry, service registry, catalog service. After that the
    consumer (CLI, Streamlit, tests) asks for whatever it needs via the
    public methods; it does not reach into the registries directly.

    Example::

        worker = GenesisWorker()
        for info in worker.list_sources():
            print(info.name, "available" if info.is_available else "missing")
        catalog = worker.rescan_catalog()
        for entry in catalog.huggingface:
            print(entry.name, entry.total_bytes)
        svc = worker.services().get("llama_swap")
        print(svc.capabilities().can_serve_llm)
    """

    def __init__(self, settings: Settings | None = None) -> None:
        # Owned by the facade. Tests / CLIs pass a pre-built Settings
        # for overrides; the default reads GENESIS_* env vars.
        self._settings = settings if settings is not None else _default_settings()

        # Auto-discovering registries — constructing each walks the
        # corresponding axis package and instantiates every concrete
        # implementation found. Adding a new source or service is one
        # new subpackage; no edits here.
        self._source_registry = SourceRegistry(self._settings)
        self._service_registry = ServiceRegistry(self._settings)

        # Catalog service uses the source registry; consumers can call
        # either ``worker.catalog_service.rescan()`` or the convenience
        # ``worker.rescan_catalog()``.
        self._catalog_service = CatalogService(self._source_registry)

        # Cached catalog. ``catalog()`` returns the most recent rescan;
        # ``rescan_catalog()`` always re-walks.
        self._catalog_cache: Catalog | None = None

    # --- Settings -----------------------------------------------------------

    @property
    def settings(self) -> Settings:
        """The :class:`Settings` this worker was constructed with."""
        return self._settings

    # --- Registries (escape hatches) ---------------------------------------

    @property
    def sources(self) -> SourceRegistry:
        """The :class:`SourceRegistry` — escape hatch for advanced consumers."""
        return self._source_registry

    @property
    def services(self) -> ServiceRegistry:
        """The :class:`ServiceRegistry` — escape hatch for advanced consumers."""
        return self._service_registry

    @property
    def catalog_service(self) -> CatalogService:
        """The :class:`CatalogService` — escape hatch for advanced consumers."""
        return self._catalog_service

    # --- Catalog ------------------------------------------------------------

    def rescan_catalog(self) -> Catalog:
        """Re-walk the vault and return the unified catalog. Updates the cache."""
        self._catalog_cache = self._catalog_service.rescan()
        return self._catalog_cache

    def catalog(self) -> Catalog:
        """Return the most recently scanned catalog, scanning on first call.

        Use :meth:`rescan_catalog` to force a fresh walk.
        """
        if self._catalog_cache is None:
            self._catalog_cache = self._catalog_service.rescan()
        return self._catalog_cache

    # --- Source / service inspection (for UI / CLI listings) ---------------

    def list_sources(self) -> list[SourceInfo]:
        """Return display info for every registered source."""
        return [
            SourceInfo(
                name=src.name,
                display_name=src.display_name,
                can_acquire=src.can_acquire,
                is_available=src.is_available(),
            )
            for src in self._source_registry.all()
        ]

    def list_services(self) -> list[ServiceInfo]:
        """Return display info for every registered service."""
        return [
            ServiceInfo(
                name=svc.name,
                display_name=svc.display_name,
                capabilities=svc.capabilities(),
            )
            for svc in self._service_registry.all()
        ]


def _default_settings() -> Settings:
    """Lazy import to avoid pulling pydantic-settings at module import time.

    Tests / CLI that build Settings explicitly don't pay this cost; only
    the no-arg ``GenesisWorker()`` path does, and it's already paying
    for the full env-var walk.
    """
    from .settings import Settings as _Settings

    return _Settings()


__all__ = ["GenesisWorker"]
