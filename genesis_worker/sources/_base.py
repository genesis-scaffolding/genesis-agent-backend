"""Model source extension axis — the :class:`ModelSource` Protocol."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import DiscoveredModel


@runtime_checkable
class ModelSource(Protocol):
    """One kind of model repository.

    Concrete sources declare:

    - ``name``: short identifier (``"huggingface"``, ``"lmstudio"``).
    - ``display_name``: human-readable name for UI.
    - ``can_acquire``: whether :class:`AcquireSession` is implemented
      (ships in spec-002).
    - ``vault_subdir``: subdirectory under ``vault_path`` where this
      source's models live (``"huggingface/hub"``,
      ``"lmstudio/models"``). The framework uses this to default
      ``local_path`` when settings don't override it.
    - ``local_path``: the resolved path the framework assigned at
      construction. Sources do not compute this themselves.

    The framework constructs each source with ``local_path=<resolved>``
    at registry-init time (see :class:`SourceRegistry`).
    """

    name: str
    display_name: str
    can_acquire: bool
    vault_subdir: str
    local_path: Path

    def is_available(self) -> bool: ...
    def walk(self) -> Sequence[DiscoveredModel]: ...


__all__ = ["ModelSource"]
