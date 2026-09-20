"""GenesisWorker — the single public facade for the worker package."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .catalog import CatalogService
from .contracts import AcquireSession, Catalog, InferenceService, ModelSource, SecretsAccessor
from .registries import ServiceRegistry, SourceRegistry
from .utils.config_overrides import read_user_overrides, write_user_overrides
from .utils.models import ServiceInfo, SettingSnapshot, SourceInfo

if TYPE_CHECKING:
    from .settings import PathsSettings, Settings

# Keys that must not be set via user-overrides.env — secrets have their
# own access path (ADR-012). Stripping them silently would make a typo
# look like a successful write; refusing loudly is the right behaviour.
_FORBIDDEN_OVERRIDE_PREFIXES = ("GENESIS_SECRETS__",)


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

    # --- User overrides + refresh (ADR-034) -------------------------------

    def user_overrides_path(self) -> Path:
        """Path to the ``user-overrides.env`` file this worker reads."""
        from .settings import user_overrides_path

        return user_overrides_path(self._settings.paths)

    def read_user_overrides(self) -> dict[str, str]:
        """Current contents of the user-overrides file as a dict.

        Empty dict when the file is absent or has no parseable keys.
        """
        return read_user_overrides(self.user_overrides_path())

    def write_user_overrides(self, values: dict[str, str]) -> None:
        """Atomic write of the user-overrides file. Validates before writing.

        Refuses keys under ``GENESIS_SECRETS__*`` — those belong to the
        secrets access path (ADR-012), not the public override layer.
        """
        bad = [k for k in values if k.startswith(_FORBIDDEN_OVERRIDE_PREFIXES)]
        if bad:
            raise ValueError(
                f"cannot set secrets via user-overrides: {bad}; use the secrets access path instead"
            )
        write_user_overrides(self.user_overrides_path(), values)

    def refresh_config(self) -> None:
        """Re-read user-overrides.env and rebuild internal state in place.

        Preserves the ``GenesisWorker`` object's identity — pages and
        routes that hold a reference continue to see the same facade.
        The catalog cache is dropped (next call rescan walks the new
        vault); the persisted ``catalog.json`` on disk is untouched.

        In-flight acquire sessions are preserved on purpose: their
        state lives on the session object itself, and the user
        explicitly asked not to drop them across a config refresh.

        Running services are unaffected — they're separate OS
        processes. ``service_status()`` re-queries tmux/docker on
        every call, so the dashboard keeps showing them as running.
        The user must restart the affected service on its own page
        to pick up new options.

        Construct-first, swap-last: if the new ``Settings()`` raises
        (bad pydantic value) or either registry raises (plugin
        construction failure), the existing facade state is intact
        and the exception propagates.
        """
        # Merge any per-path overrides on top of the existing
        # ``PathsSettings`` so the constructor kwargs don't outvote
        # the override file. Other layers (real env, .env) still
        # take effect because we don't touch them here.
        merged_paths = _merge_paths_with_overrides(self._settings.paths)
        new_settings = _build_settings(paths=merged_paths)
        new_sources = SourceRegistry(new_settings)
        new_services = ServiceRegistry(new_settings)
        new_catalog_service = CatalogService(
            new_sources,
            catalog_path=new_settings.paths.state_dir / "catalog.json",
        )
        # Atomic swap — only after every constructor succeeded.
        self._settings = new_settings
        self._source_registry = new_sources
        self._service_registry = new_services
        self._catalog_service = new_catalog_service
        self._catalog_cache = None

    def snapshot_settings(self) -> list[SettingSnapshot]:
        """Framework knobs the Settings page renders, with their resolved
        values and the precedence layer each one came from.

        Returns settings the page actually exposes today: paths and
        per-source ``local_path``s. Service-specific knobs stay on
        each service's own page (ADR-034).
        """
        from .settings import user_overrides_path

        paths = self._settings.paths
        env_overrides = read_user_overrides(user_overrides_path(paths))
        snapshots: list[SettingSnapshot] = []

        # Path knobs. Each renders as an editable text field.
        path_knobs: list[tuple[str, Path, str]] = [
            (
                "vault_path",
                paths.vault_path or paths.resolved_vault_path,
                "GENESIS_PATHS__VAULT_PATH",
            ),
            (
                "media_vault_path",
                paths.media_vault_path or paths.resolved_media_vault_path,
                "GENESIS_PATHS__MEDIA_VAULT_PATH",
            ),
            ("data_dir", paths.data_dir, "GENESIS_PATHS__DATA_DIR"),
            ("config_dir", paths.config_dir, "GENESIS_PATHS__CONFIG_DIR"),
            ("cache_dir", paths.cache_dir, "GENESIS_PATHS__CACHE_DIR"),
            ("state_dir", paths.state_dir, "GENESIS_PATHS__STATE_DIR"),
            ("log_dir", paths.log_dir, "GENESIS_PATHS__LOG_DIR"),
        ]
        for name, value, env_key in path_knobs:
            source = self._classify_source(env_key, env_overrides, value)
            snapshots.append(
                SettingSnapshot(
                    name=f"paths.{name}",
                    value=value,
                    source=source,
                    override_key=env_key,
                    override_path=value,
                )
            )

        # Per-source local_path knobs. Use the resolved local_path the
        # registry actually built for each source, so the UI shows
        # what's in effect rather than what's configured (the framework
        # may have applied vault_subdir or the legacy MODELS_ROOT
        # fallback).
        for info in self.list_sources():
            src = self.source(info.name)
            env_key = f"GENESIS_SOURCES__{info.name.upper()}__LOCAL_PATH"
            source = self._classify_source(env_key, env_overrides, src.local_path)
            snapshots.append(
                SettingSnapshot(
                    name=f"sources.{info.name}.local_path",
                    value=src.local_path,
                    source=source,
                    override_key=env_key,
                    override_path=src.local_path,
                )
            )

        return snapshots

    @staticmethod
    def _classify_source(env_key: str, env_overrides: dict[str, str], resolved: Any) -> str:
        """Where the resolved value of one knob came from.

        The introspection is approximate: pydantic-settings doesn't
        expose per-key source attribution, so we walk the override
        file ourselves and label accordingly. Real env > override
        file > ``.env`` > default. We only label "user_overrides"
        when the override file actually carries that key.
        """
        import os

        if env_key in os.environ:
            return "env"
        if env_key in env_overrides:
            return "user_overrides"
        # We can't easily distinguish ``.env`` from defaults without
        # re-walking ``.env``. The page treats unknown provenance as
        # "default"; users can spot a value they think came from
        # elsewhere and inspect the file by hand.
        return "default"

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
            from .utils.catalog_io import load_catalog

            loaded = load_catalog(self._settings.paths.state_dir / "catalog.json")
            if loaded is not None:
                self._catalog_cache = loaded
            else:
                self._catalog_cache = self._catalog_service.rescan()
        return self._catalog_cache

    def delete_model(self, source: str, name: str) -> None:
        """Delete ``name`` from ``source``: removes entry from catalog and wipes the directory.

        Raises:
            ValueError: no entry matching (source, name) exists.
        """
        from .utils.catalog_io import save_catalog

        catalog = self.catalog()
        entry = next(
            (e for e in catalog.entries if e.source == source and e.name == name),
            None,
        )
        if entry is None:
            raise ValueError(f"No entry found for {source}/{name}")

        directory = Path(entry.directory)
        if directory.exists():
            shutil.rmtree(directory)

        catalog.entries.remove(entry)
        save_catalog(self._settings.paths.state_dir / "catalog.json", catalog)

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
        return session.view()

    def submit_acquire(self, session: AcquireSession, choice):
        session.submit(choice)

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
                view = sess.view()
            except Exception:  # noqa: BLE001 — stale session; skip
                self._sessions.pop(sid, None)
                continue
            if view.kind in _TERMINAL_KINDS:
                self._sessions.pop(sid, None)
                continue
            if source_name is not None and src != source_name:
                continue
            out.append(
                {
                    "id": sid,
                    "source": src,
                    "repo_id": getattr(sess, "repo_id", "?"),
                    "state": view.kind,
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
        """Return display info for every registered service, regardless of enabled state.

        Used by the Service Catalog page (a meta-view that must show
        disabled services too). For the dashboard and sidebar use
        :meth:`list_enabled_services`.
        """
        return [
            ServiceInfo(
                name=svc.name,
                display_name=svc.display_name,
                capabilities=svc.capabilities(),
                category=svc.category,
                description=svc.description,
            )
            for svc in self._service_registry.all()
        ]

    def list_enabled_services(self) -> list[ServiceInfo]:
        """Return display info for enabled services only.

        The dashboard and the sidebar both filter to enabled services
        (ADR-029). Disabled services are absent from these surfaces and
        can only be reached via the Service Catalog page.
        """
        enabled = self._service_registry.enabled_names()
        return [
            ServiceInfo(
                name=svc.name,
                display_name=svc.display_name,
                capabilities=svc.capabilities(),
                category=svc.category,
                description=svc.description,
            )
            for svc in self._service_registry.all()
            if svc.name in enabled
        ]

    def start_service(self, name: str):
        return self._service_registry.get(name).start()

    def stop_service(self, name: str):
        return self._service_registry.get(name).stop()

    def service_status(self, name: str):
        return self._service_registry.get(name).status()

    def rebuild_service(self, name: str) -> InferenceService:
        """Re-construct a single service against current options.

        Refuses if the service is currently running — stop it first.
        The ``configure`` panel's ``Apply`` button calls this after
        persisting user-overrides; ``Apply & restart`` wraps it with
        stop + start so the new config takes effect immediately.
        """
        return self._service_registry.rebuild(name)

    def read_service_overrides(self, name: str) -> dict[str, Any]:
        """Read the per-service override JSON sidecar for ``name``.

        Returns the raw dict (which may contain map-typed entries
        that don't fit the flat ``user-overrides.env`` shape). The
        ``configure`` panel merges these into the form's current
        values alongside the flat-key overrides.
        """
        from .utils.config_overrides import read_service_overrides, service_overrides_path

        return read_service_overrides(service_overrides_path(self._settings.paths.config_dir, name))

    def write_service_overrides(self, name: str, values: dict[str, Any]) -> None:
        """Atomic write of the per-service override JSON sidecar."""
        from .utils.config_overrides import service_overrides_path
        from .utils.config_overrides import write_service_overrides as _w

        _w(service_overrides_path(self._settings.paths.config_dir, name), values)

    def collect_metrics(self):
        from .utils.collectors.metrics import collect_metrics as _collect

        return _collect()

    def collect_host_info(self):
        from .utils.collectors.host_info import collect_host_info as _collect

        return _collect()


def _build_settings(paths: PathsSettings | None = None) -> Settings:
    """Build a fresh ``Settings`` instance from current env + overrides.

    Used by both the initial ``GenesisWorker()`` path and by
    :meth:`GenesisWorker.refresh_config`. Centralised so the two
    call sites construct identically.

    ``paths`` lets the caller preserve a custom path layout across
    refreshes — tests and embedded uses pass their hermetic layout;
    production callers omit it and let env / XDG defaults apply.
    """
    from .settings import Settings as _Settings

    if paths is None:
        return _Settings()
    return _Settings(paths=paths)


def _merge_paths_with_overrides(paths: PathsSettings) -> PathsSettings:
    """Re-read the override file and apply any per-path keys on top
    of the existing ``PathsSettings``.

    The Settings page's framework knobs all flow through here:
    ``GENESIS_PATHS__VAULT_PATH``, ``GENESIS_PATHS__DATA_DIR``, etc.
    Per-source ``local_path`` knobs are not path-level fields on
    ``PathsSettings`` — they're sources-level options, picked up by
    the registry via ``ctx.options`` after Settings construction.

    Without this merge, ``Settings(paths=...)`` constructor kwargs
    would outvote the override file (constructor > file per ADR
    precedence). The override file is supposed to win over the
    initial ``PathsSettings`` we constructed the worker with.
    """
    from .settings import USER_OVERRIDES_FILENAME
    from .settings import PathsSettings as _PS

    overrides = read_user_overrides(paths.config_dir / USER_OVERRIDES_FILENAME)
    if not overrides:
        return paths
    fields = paths.model_dump()
    mapping = {
        "GENESIS_PATHS__VAULT_PATH": "vault_path",
        "GENESIS_PATHS__MEDIA_VAULT_PATH": "media_vault_path",
        "GENESIS_PATHS__DATA_DIR": "data_dir",
        "GENESIS_PATHS__CONFIG_DIR": "config_dir",
        "GENESIS_PATHS__CACHE_DIR": "cache_dir",
        "GENESIS_PATHS__STATE_DIR": "state_dir",
        "GENESIS_PATHS__LOG_DIR": "log_dir",
    }
    for env_key, field_name in mapping.items():
        if env_key in overrides:
            fields[field_name] = overrides[env_key]
    return _PS(**fields)


def _default_settings() -> Settings:
    """Backward-compat alias for :func:`_build_settings`.

    Kept as a separate name to preserve the existing call site's
    readability (``GenesisWorker.__init__`` reads
    ``_default_settings()`` more clearly than ``_build_settings()``).
    """
    return _build_settings()


__all__ = ["GenesisWorker"]
