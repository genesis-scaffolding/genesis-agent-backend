"""Model source extension axis — the :class:`ModelSource` Protocol.

A :class:`ModelSource` knows how to discover (and, optionally, acquire)
models from one kind of repository: HuggingFace cache, LM Studio layout,
ModelScope, Civitai, etc.

Sources are pure logic: the framework constructs each source with a
fully-resolved ``local_path`` (see
:class:`~genesis_worker.sources._registry.SourceRegistry`) and the
source walks that path. Sources do NOT import ``xdg_path`` or compute
paths themselves — they declare their on-disk layout via the
``vault_subdir`` class attribute (e.g. ``"huggingface/hub"``) and the
framework decides where that lands.

Adding a new source is one new module under ``genesis_worker/sources/``:
import the class and pass it to :class:`SourceRegistry` explicitly.
There is no auto-discovery and no decorator.

The discovery entity types (:class:`~genesis_worker.models.DiscoveredModel`,
:class:`~genesis_worker.models.ModelPiece`) live at the framework level
in :mod:`genesis_worker.models`, not here — sources emit them, the
catalog consumes them, and other axes may too.

ADR-003 details the extension architecture.
"""

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
