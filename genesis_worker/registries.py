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
        """
        return {
            "name": cls.name,
            "repo_root": self._settings.paths.resolved_repo_root,
            "secrets": self._settings.secrets.accessor(),
            "vault_path": self._settings.paths.resolved_vault_path,
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
    """Constructs and holds one instance of every discovered service plugin."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        for cls in _plugin_classes(_SERVICES_PKG, InferenceService):
            build = cast("Callable[[ServiceContext], InferenceService]", cls)
            self._instances[cls.name] = build(self._context(cls))

    def _context(self, cls: type[InferenceService]) -> ServiceContext:
        return ServiceContext(
            options=self._settings.options_for("services", cls.name),
            **self._common_kwargs(cls),
        )

    def get(self, name: str) -> InferenceService:
        return self._instances[name]  # type: ignore[return-value]

    def all(self) -> list[InferenceService]:
        return list(self._instances.values())  # type: ignore[arg-type]


__all__ = ["ServiceRegistry", "SourceRegistry"]
