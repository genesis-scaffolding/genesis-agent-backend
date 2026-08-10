"""Source registry — facade over :class:`ModelSource` construction."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

from ._base import ModelSource

if TYPE_CHECKING:
    from ..settings import Settings


def _find_extension_class(module, *required_attrs: str) -> type | None:
    """Find the first class in ``module`` that declares every required attribute.

    Used by both :class:`SourceRegistry` and :class:`ServiceRegistry` to
    locate the concrete extension class after auto-discovering a
    subpackage. Returns ``None`` if no such class is found.
    """
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if not isinstance(attr, type):
            continue
        if not all(hasattr(attr, a) for a in required_attrs):
            continue
        return attr
    return None


class SourceRegistry:
    """Facade for constructing and looking up :class:`ModelSource` instances.

    On construction, the registry walks the sibling subpackages of
    ``genesis_worker.sources`` and instantiates one of every concrete
    source found. Each source is constructed with ``local_path``
    resolved from settings.

    Example::

        registry = SourceRegistry(Settings())
        for source in registry.all():
            if source.is_available():
                ...
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, ModelSource] = {}
        self._discover()

    def _discover(self) -> None:
        """Walk sibling subpackages of ``genesis_worker.sources``.

        Skips packages whose name starts with ``_`` (private helpers,
        protocols, this registry itself). For each remaining subpackage,
        imports it (triggering its ``__init__`` re-export) and looks for
        a class declaring both ``name`` and ``vault_subdir``.
        """
        pkg = importlib.import_module(__package__ or "")
        assert pkg.__path__ is not None
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            name = mod_info.name
            if not name or name.startswith("_"):
                continue
            sub = importlib.import_module(f"{pkg.__name__}.{name}")
            cls = _find_extension_class(sub, "name", "vault_subdir")
            if cls is None:
                continue
            self._instances[cls.name] = cls(local_path=self._resolve_path(cls))

    def _resolve_path(self, cls: type) -> Path:
        """Resolve ``local_path`` for one source from settings.

        Three-tier resolution: explicit absolute > explicit relative >
        ``vault_subdir`` default. The framework, not the source, owns
        this logic.
        """
        vault_root = self._settings.paths.resolved_vault_path
        # Per-source settings slice. Sources without a settings slice
        # fall straight through to the vault_subdir default.
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
        """Look up one source by its ``name`` attribute. Raises ``KeyError`` if unknown."""
        return self._instances[name]

    def all(self) -> list[ModelSource]:
        """Return every registered source as a list, in registration order."""
        return list(self._instances.values())

    @property
    def vault_path(self) -> Path:
        """The resolved vault root, derived from settings."""
        return self._settings.paths.resolved_vault_path


__all__ = ["SourceRegistry"]
