"""Model source extension axis — the :class:`ModelSource` interface."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from pathlib import Path

from .acquire import AcquireSession
from .catalog import DiscoveredModel
from .context import SourceContext
from .plugin import Plugin


class ModelSource(Plugin):
    """One kind of model repository.

    ``vault_subdir`` is what the framework defaults ``local_path`` to when settings
    don't override it. Everything else a source needs arrives on the context.
    """

    can_acquire: bool = False
    vault_subdir: str

    def __init__(self, ctx: SourceContext) -> None:
        super().__init__(ctx)
        self._ctx: SourceContext = ctx

    @property
    def local_path(self) -> Path:
        return self._ctx.local_path

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def walk(self) -> Sequence[DiscoveredModel]: ...

    def start_acquire(self, repo_id: str) -> AcquireSession:
        """Begin acquiring ``repo_id``. Only meaningful when ``can_acquire``."""
        raise NotImplementedError(f"{self.name} does not support acquisition")


__all__ = ["ModelSource"]
