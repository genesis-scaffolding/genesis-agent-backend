"""Plugin registries — the framework's construction point for sources and services."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from .contracts import InferenceService, ModelSource

if TYPE_CHECKING:
    from .settings import Settings

_SOURCES_PKG = "genesis_worker.sources"
_SERVICES_PKG = "genesis_worker.services"


def _find_extension_class(module: ModuleType, *required_attrs: str) -> type | None:
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if not isinstance(attr, type):
            continue
        if not all(hasattr(attr, a) for a in required_attrs):
            continue
        return attr
    return None


def _plugin_modules(package: str):
    """Import each non-private subpackage of ``package`` and yield it."""
    pkg = importlib.import_module(package)
    assert pkg.__path__ is not None
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        name = mod_info.name
        if not name or name.startswith("_"):
            continue
        yield importlib.import_module(f"{package}.{name}")


class SourceRegistry:
    """Constructs and holds one instance of every discovered source plugin."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, ModelSource] = {}
        self._discover()

    def _discover(self) -> None:
        for module in _plugin_modules(_SOURCES_PKG):
            cls = _find_extension_class(module, "name", "vault_subdir")
            if cls is None:
                continue
            self._instances[cls.name] = cls(local_path=self._resolve_path(cls))

    def _resolve_path(self, cls: type) -> Path:
        """Explicit absolute > explicit relative to vault > ``vault_subdir`` default."""
        vault_root = self._settings.paths.resolved_vault_path
        per_source = getattr(self._settings.sources, cls.name, None)
        local_path: Path | None = None
        if per_source is not None:
            local_path = getattr(per_source, "local_path", None)
        if local_path is not None:
            if local_path.is_absolute():
                return local_path
            return vault_root / local_path
        return vault_root / cls.vault_subdir

    def get(self, name: str) -> ModelSource:
        return self._instances[name]

    def all(self) -> list[ModelSource]:
        return list(self._instances.values())

    @property
    def vault_path(self) -> Path:
        return self._settings.paths.resolved_vault_path


class ServiceRegistry:
    """Constructs and holds one instance of every discovered service plugin."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, InferenceService] = {}
        self._discover()

    def _discover(self) -> None:
        for module in _plugin_modules(_SERVICES_PKG):
            cls = _find_extension_class(module, "name", "display_name")
            if cls is None:
                continue
            per_service = getattr(self._settings.services, cls.name, None)
            self._instances[cls.name] = cls(settings=per_service)

    def get(self, name: str) -> InferenceService:
        return self._instances[name]

    def all(self) -> list[InferenceService]:
        return list(self._instances.values())


__all__ = ["ServiceRegistry", "SourceRegistry"]
