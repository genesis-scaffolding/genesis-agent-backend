"""Source registry — facade over :class:`ModelSource` construction.

The :class:`SourceRegistry` is the single point of construction for
sources. It takes a list of source *classes* (no decorator, no
auto-discovery) and a :class:`~genesis_worker.settings.Settings` object,
resolves each source's ``local_path`` from settings, constructs the
source with that resolved path, and exposes ``.get(name)`` / ``.all()``.

Path-resolution rules (highest priority first):

1. ``settings.sources.<name>.local_path`` set to an absolute path → use as-is.
2. ``settings.sources.<name>.local_path`` set to a relative path → join
   with ``settings.paths.resolved_vault_path``.
3. No override → ``settings.paths.resolved_vault_path / source.vault_subdir``.

Adding a new source requires a settings field on
:class:`~genesis_worker.settings.SourcesSettings` if the path should be
user-overridable; otherwise the ``vault_subdir`` default is enough.

Sources are pure logic and never import ``xdg_path`` or compute paths
themselves — that is the framework's responsibility.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ._base import ModelSource

if TYPE_CHECKING:
    from ..settings import Settings


class SourceRegistry:
    """Facade for constructing and looking up :class:`ModelSource` instances.

    Construction is explicit: callers pass the source classes they want
    registered. No decorators, no module-level state, no auto-discovery.

    Example::

        registry = SourceRegistry(
            Settings(),
            [HuggingFaceSource, LMSource],
        )
        for source in registry.all():
            ...

    Each source is constructed exactly once with ``local_path`` resolved
    from settings per the rules in the module docstring.
    """

    def __init__(self, settings: Settings, source_classes: Iterable[type]) -> None:
        self._settings = settings
        self._instances: dict[str, ModelSource] = {}
        for cls in source_classes:
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
