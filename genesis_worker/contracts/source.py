"""Model source extension axis — the :class:`ModelSource` interface."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from .catalog import DiscoveredModel


@runtime_checkable
class ModelSource(Protocol):
    """One kind of model repository.

    ``vault_subdir`` is what the framework defaults ``local_path`` to when settings
    don't override it. ``local_path`` is assigned by the framework at construction;
    sources never resolve it themselves.
    """

    name: str
    display_name: str
    can_acquire: bool
    vault_subdir: str
    local_path: Path

    def is_available(self) -> bool: ...
    def walk(self) -> Sequence[DiscoveredModel]: ...


__all__ = ["ModelSource"]
