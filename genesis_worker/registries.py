"""Plugin registries — the framework's construction point for sources and services."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, TypeVar, cast

from .contracts import (
    InferenceService,
    ModelSource,
    Plugin,
    ServiceContext,
    SourceContext,
)
from .utils.state.enabled_services import load_enabled_set, save_enabled_set

if TYPE_CHECKING:
    from .settings import Settings

_SOURCES_PKG = "genesis_worker.sources"
_SERVICES_PKG = "genesis_worker.services"

P = TypeVar("P", bound=Plugin)


def _plugin_classes(package: str, base: type[P]) -> Iterator[type[P]]:
    """Yield each concrete ``base`` subclass exported by a subpackage of ``package``."""
    pkg = importlib.import_module(package)
    assert pkg.__path__ is not None
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        name = mod_info.name
        if not name or name.startswith("_"):
            continue
        module = importlib.import_module(f"{package}.{name}")
        cls = _find_plugin_class(module, base)
        if cls is not None:
            yield cls


def _declarative_spec_paths(services_pkg: str) -> list[Path]:
    """Every ``*.yaml`` under ``<services_pkg>/_declarative/``.

    Phase 3 will populate ``services/_declarative/`` with the real
    service YAMLs; phase 2 only walks an empty marker package, so the
    helper exists but yields nothing yet.
    """
    pkg = importlib.import_module(services_pkg)
    assert pkg.__path__ is not None
    pkg_path = Path(pkg.__path__[0])
    declarative_dir = pkg_path / "_declarative"
    if not declarative_dir.is_dir():
        return []
    return sorted(p for p in declarative_dir.glob("*.yaml") if p.is_file())


def _find_plugin_class(module: ModuleType, base: type[P]) -> type[P] | None:
    for _, attr in inspect.getmembers(module, inspect.isclass):
        if issubclass(attr, base) and attr is not base and not inspect.isabstract(attr):
            return attr
    return None


class _Registry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, Plugin] = {}

    def _dirs(self, cls: type[Plugin]) -> dict[str, Path]:
        p = self._settings.paths
        return {
            "data_dir": p.data_dir / cls.dir_name,
            "config_dir": p.config_dir / cls.dir_name,
            "cache_dir": p.cache_dir / cls.dir_name,
            "state_dir": p.state_dir / cls.dir_name,
            "log_dir": p.log_dir / cls.dir_name,
        }

    def _common_kwargs(self, cls: type[Plugin]) -> dict:
        """Fields every PluginContext receives.

        Both SourceRegistry and ServiceRegistry use this so the
        source-side ``_resolve_local_path`` and the service-side
        options lookup remain the only per-axis differences (ADR-023).

        ``host_info`` is shared across every plugin so a single
        snapshot — including hardware probes that are best done
        once per worker startup — covers all consumers.
        """
        from .utils.collectors.host_info import collect_host_info

        return {
            "name": cls.name,
            "repo_root": self._settings.paths.resolved_repo_root,
            "secrets": self._settings.secrets.accessor(),
            "vault_path": self._settings.paths.resolved_vault_path,
            "host_info": collect_host_info(),
            **self._dirs(cls),
        }

    def get(self, name: str):
        return self._instances[name]

    def all(self) -> list:
        return list(self._instances.values())


class SourceRegistry(_Registry):
    """Constructs and holds one instance of every discovered source plugin."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        for cls in _plugin_classes(_SOURCES_PKG, ModelSource):
            build = cast("Callable[[SourceContext], ModelSource]", cls)
            self._instances[cls.name] = build(self._context(cls))

    def _context(self, cls: type[ModelSource]) -> SourceContext:
        options = self._settings.options_for("sources", cls.name)
        return SourceContext(
            local_path=self._resolve_local_path(cls, options),
            options=options,
            **self._common_kwargs(cls),
        )

    def _resolve_local_path(self, cls: type[ModelSource], options: dict) -> Path:
        """Explicit absolute > explicit relative to vault > ``vault_subdir`` default."""
        raw = options.get("local_path")
        if raw is not None:
            local_path = Path(raw)
            if local_path.is_absolute():
                return local_path
            return self.vault_path / local_path
        return self.vault_path / cls.vault_subdir

    def get(self, name: str) -> ModelSource:
        return self._instances[name]  # type: ignore[return-value]

    def all(self) -> list[ModelSource]:
        return list(self._instances.values())  # type: ignore[arg-type]

    @property
    def vault_path(self) -> Path:
        return self._settings.paths.resolved_vault_path


class ServiceRegistry(_Registry):
    """Constructs and holds one instance of every discovered service plugin.

    Also owns the per-service enabled/disabled set (ADR-029). The set is
    persisted at ``<state_dir>/enabled_services.yaml``; on first run
    (no file) we bootstrap by auto-enabling every service that reports
    ``is_available() == True``. After that the set is user-controlled.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        # Python plugins first (they win duplicate-name conflicts so a
        # service can pin a YAML aside while keeping the Python class).
        for cls in _plugin_classes(_SERVICES_PKG, InferenceService):
            build = cast("Callable[[ServiceContext], InferenceService]", cls)
            self._instances[cls.name] = build(self._context(cls))
        # Spec-path map for declarative services so ``rebuild`` can
        # re-run the loader without re-walking the YAMLs (ADR-036).
        self._spec_paths: dict[str, Path] = {}
        # Then declarative YAML specs (ADR-035 phase 2).
        from .utils.services import load_service_spec

        for spec_path in _declarative_spec_paths(_SERVICES_PKG):
            ctx = self._context_for_name(spec_path.stem)
            svc = load_service_spec(spec_path, ctx=ctx)
            if svc.name in self._instances:
                raise ValueError(
                    f"duplicate service name {svc.name!r}: "
                    f"already registered from a Python plugin before {spec_path.name}"
                )
            self._instances[svc.name] = svc
            self._spec_paths[svc.name] = spec_path
        self._enabled: set[str] = self._load_or_bootstrap_enabled_set()

    def _context_for_name(self, name: str) -> ServiceContext:
        """Build a ``ServiceContext`` keyed by ``name``.

        Used by both the Python-plugin path (via ``_context(cls)``)
        and the YAML loader (which only has a name string, no class).
        """
        from .utils.collectors.host_info import collect_host_info

        p = self._settings.paths
        return ServiceContext(
            name=name,
            repo_root=p.resolved_repo_root,
            vault_path=p.resolved_vault_path,
            media_vault_path=p.resolved_media_vault_path,
            host_info=collect_host_info(),
            secrets=self._settings.secrets.accessor(),
            options=self._settings.options_for("services", name),
            data_dir=p.data_dir / name.replace("_", "-"),
            config_dir=p.config_dir / name.replace("_", "-"),
            cache_dir=p.cache_dir / name.replace("_", "-"),
            state_dir=p.state_dir / name.replace("_", "-"),
            log_dir=p.log_dir / name.replace("_", "-"),
        )

    def _context(self, cls: type[InferenceService]) -> ServiceContext:
        return self._context_for_name(cls.name)

    def _load_or_bootstrap_enabled_set(self) -> set[str]:
        """Read the persisted enabled set, or bootstrap on first run.

        Bootstrap rule (ADR-029): on first run (no state file), enable
        every service that reports ``is_available() == True``. This
        matches the user-facing intent ("out of the gate, services are
        disabled until enabled, or already installed and ready to run").
        One-shot — a service installed after bootstrap stays disabled
        until the user enables it on the Service Catalog page.
        """
        existing = load_enabled_set(self._settings.paths.state_dir)
        if existing is not None:
            return existing
        services = cast("dict[str, InferenceService]", self._instances)
        to_enable = {name for name, svc in services.items() if svc.is_available()}
        save_enabled_set(self._settings.paths.state_dir, to_enable)
        return to_enable

    def get(self, name: str) -> InferenceService:
        return self._instances[name]  # type: ignore[return-value]

    def all(self) -> list[InferenceService]:
        return list(self._instances.values())  # type: ignore[arg-type]

    # --- enable / disable (ADR-029) --------------------------------------

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def enabled_names(self) -> set[str]:
        return set(self._enabled)

    def enable(self, name: str) -> None:
        """Mark a service enabled. Idempotent. Raises on unknown service."""
        if name not in self._instances:
            raise KeyError(f"unknown service: {name}")
        if name in self._enabled:
            return
        self._enabled.add(name)
        save_enabled_set(self._settings.paths.state_dir, self._enabled)

    def disable(self, name: str) -> None:
        """Mark a service disabled. Refuses when the service is running.

        The running-guard enforces "only services that are not currently
        on can be turned off" at the framework level — a buggy UI can't
        sidestep it. Stops-then-disables is the correct user flow.
        """
        svc = cast("dict[str, InferenceService]", self._instances)[name]
        if svc.is_running():
            raise RuntimeError(
                f"cannot disable {svc.display_name}: service is running — stop it first"
            )
        # Idempotent: if already disabled, just skip the persist.
        if name not in self._enabled:
            return
        self._enabled.discard(name)
        save_enabled_set(self._settings.paths.state_dir, self._enabled)

    def enabled(self) -> list[InferenceService]:
        """Enabled services in registration order. Used by dashboard + sidebar."""
        services = cast("dict[str, InferenceService]", self._instances)
        return [svc for name, svc in services.items() if name in self._enabled]

    def disabled(self) -> list[InferenceService]:
        """Disabled services in registration order. Used by the catalog page."""
        services = cast("dict[str, InferenceService]", self._instances)
        return [svc for name, svc in services.items() if name not in self._enabled]

    # --- rebuild (ADR-036) -----------------------------------------------

    def rebuild(self, name: str) -> InferenceService:
        """Re-construct ``name`` against current options. Refuses if running.

        Construct-first, swap-last: if the loader raises, the existing
        cached instance is left untouched and the exception propagates.
        The caller (``worker.rebuild_service`` / the ``configure``
        panel's Apply button) is responsible for stopping the service
        first; ``Apply & restart`` wraps this with stop + start.

        Python services (no YAML spec path) raise ``KeyError``; the
        configure panel doesn't render for them so this branch is
        unreachable in practice, but the type system surfaces the
        constraint.
        """
        spec_path = self._spec_paths.get(name)
        if spec_path is None:
            raise KeyError(
                f"cannot rebuild {name!r}: no declarative YAML spec path; "
                "Python services rebuild via their own lifecycle"
            )
        old = self._instances.get(name)
        if old is not None and old.is_running():
            raise RuntimeError(f"cannot rebuild {name!r} while running — stop the service first")
        from .utils.services import load_service_spec

        new_svc = load_service_spec(spec_path, ctx=self._context_for_name(name))
        self._instances[name] = new_svc
        return new_svc


__all__ = ["ServiceRegistry", "SourceRegistry"]
