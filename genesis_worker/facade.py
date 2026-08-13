"""GenesisWorker — the single public facade for the worker package."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from .catalog_build import CatalogService
from .contracts import AcquireSession, Catalog, InferenceService, ModelSource, SecretsAccessor
from .models import ServiceInfo, SourceInfo
from .registries import ServiceRegistry, SourceRegistry

if TYPE_CHECKING:
    from .settings import Settings


_TERMINAL_KINDS = frozenset({"complete", "failed", "cancelled"})


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
        # ``worker.rescan_catalog()``. The catalog is persisted at
        # ``state_dir/catalog.json`` so its ``generated_at`` is stable across
        # streamlit restarts when the vault hasn't changed (ADR-011).
        self._catalog_service = CatalogService(
            self._source_registry,
            catalog_path=self._settings.paths.state_dir / "catalog.json",
        )

        # Cached catalog. ``catalog()`` returns the most recent rescan;
        # ``rescan_catalog()`` always re-walks.
        self._catalog_cache: Catalog | None = None

        # Active acquire sessions, keyed by an opaque id. Pages and CLIs
        # call ``start_acquire`` and store the returned session object;
        # the facade also tracks them centrally so other surfaces (the
        # session_list page, the CLI) can list and cancel them.
        self._sessions: dict[str, tuple[str, AcquireSession]] = {}

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

    @property
    def secrets(self) -> SecretsAccessor:
        """Framework-managed secrets accessor (ADR-012).

        Plugins read secrets via ``ctx.secrets.get(name)``; this method is
        for tests and CLIs that need direct access.
        """
        return self._settings.secrets.accessor()

    def secret(self, name: str) -> str | None:
        """Convenience: ``self.secrets.get(name)``."""
        return self._settings.secrets.accessor().get(name)

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
            # Try the persisted file first; fall back to a fresh rescan.
            from .catalog_io import load_catalog

            loaded = load_catalog(self._settings.paths.state_dir / "catalog.json")
            if loaded is not None:
                self._catalog_cache = loaded
            else:
                self._catalog_cache = self._catalog_service.rescan()
        return self._catalog_cache

    # --- Source / service inspection (for UI / CLI listings) ---------------

    def source(self, name: str) -> ModelSource:
        return self._source_registry.get(name)

    def service(self, name: str) -> InferenceService:
        return self._service_registry.get(name)

    def start_acquire(self, source_name: str, repo_id: str) -> AcquireSession:
        """Begin acquiring ``repo_id`` from ``source_name``.

        The session is registered centrally so ``list_acquire_sessions``
        and the per-source session-list page can see it. The caller
        keeps a direct reference to the session object; cancellation and
        step retrieval work on that reference, not on the id.
        """
        session = self._source_registry.get(source_name).start_acquire(repo_id)
        sid = uuid.uuid4().hex
        session._facade_id = sid  # type: ignore[attr-defined]
        self._sessions[sid] = (source_name, session)
        return session

    def acquire_step(self, session: AcquireSession):
        return session.current_step()

    def submit_acquire(self, session: AcquireSession, choice):
        return session.submit(choice)

    def cancel_acquire(self, session: AcquireSession) -> None:
        """Cancel an in-flight session. Idempotent."""
        session.cancel()

    def list_acquire_sessions(self, source_name: str | None = None) -> list[dict]:
        """Return summaries of non-terminal sessions.

        Each entry: ``id``, ``source``, ``repo_id``, ``state``, ``session``.
        Terminal sessions (complete / failed / cancelled) are dropped from
        the registry as a side effect.
        """
        out: list[dict] = []
        for sid, (src, sess) in list(self._sessions.items()):
            try:
                step = sess.current_step()
            except Exception:  # noqa: BLE001 — stale session; skip
                self._sessions.pop(sid, None)
                continue
            if step.kind in _TERMINAL_KINDS:
                self._sessions.pop(sid, None)
                continue
            if source_name is not None and src != source_name:
                continue
            out.append(
                {
                    "id": sid,
                    "source": src,
                    "repo_id": getattr(sess, "repo_id", "?"),
                    "state": step.kind,
                    "session": sess,
                }
            )
        return out

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

    def collect_host_info(self):
        from .host_info import collect_host_info as _collect

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
