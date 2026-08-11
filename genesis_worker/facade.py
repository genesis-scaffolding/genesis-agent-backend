"""GenesisWorker — the single public facade for the worker package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog_build import CatalogService
from .contracts import AcquireSession, Catalog, InferenceService, ModelSource
from .models import ServiceInfo, SourceInfo
from .registries import ServiceRegistry, SourceRegistry

if TYPE_CHECKING:
    from .settings import Settings


class GenesisWorker:
    """Top-level facade for the worker."""

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

    def source(self, name: str) -> ModelSource:
        return self._source_registry.get(name)

    def service(self, name: str) -> InferenceService:
        return self._service_registry.get(name)

    def start_acquire(self, source_name: str, repo_id: str) -> AcquireSession:
        """Begin acquiring ``repo_id`` from ``source_name``."""
        return self._source_registry.get(source_name).start_acquire(repo_id)

    def acquire_step(self, session: AcquireSession):
        return session.current_step()

    def submit_acquire(self, session: AcquireSession, choice):
        return session.submit(choice)

    def cancel_acquire(self, session: AcquireSession) -> None:
        session.cancel()

    def list_acquire_sessions(self, source_name: str | None = None) -> list[dict]:
        """Return summaries of in-flight sessions, optionally filtered by source."""
        return []  # Sources don't currently track past sessions; per-page state only.

    def regenerate_service_config(self, service_name: str) -> bool:
        """Regenerate one service's config against the current catalog."""
        return self._service_registry.get(service_name).regenerate_config(self.catalog())

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

    def start_service(self, name: str):
        return self._service_registry.get(name).start()

    def stop_service(self, name: str):
        return self._service_registry.get(name).stop()

    def service_status(self, name: str):
        return self._service_registry.get(name).status()

    def collect_metrics(self):
        from .metrics import collect_metrics as _collect

        return _collect()


def _default_settings() -> Settings:
    """Lazy import to avoid pulling pydantic-settings at module import time.

    Tests / CLI that build Settings explicitly don't pay this cost; only
    the no-arg ``GenesisWorker()`` path does, and it's already paying
    for the full env-var walk.
    """
    from .settings import Settings as _Settings

    return _Settings()


__all__ = ["GenesisWorker"]
