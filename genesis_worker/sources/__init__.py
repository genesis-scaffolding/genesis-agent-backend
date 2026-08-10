"""Model source extension axis.

The :class:`SourceRegistry` facade is the single point of construction
for :class:`ModelSource` instances. Sources are pure logic — the
framework sets their ``local_path`` and they walk it.

Typical usage::

    from genesis_worker.sources import (
        HuggingFaceSource,
        LMSource,
        SourceRegistry,
    )
    from genesis_worker.settings import Settings

    registry = SourceRegistry(Settings(), [HuggingFaceSource, LMSource])
    for source in registry.all():
        if source.is_available():
            for model in source.walk():
                ...

Adding a new source is one new module under this package; the only
other change is to add the class to the list passed to
:class:`SourceRegistry`. ADR-003 details the extension architecture.
"""

from ..models import DiscoveredModel, ModelPiece
from ._base import ModelSource
from ._registry import SourceRegistry
from .huggingface import HuggingFaceSource
from .lmstudio import LMSource

__all__ = [
    "DiscoveredModel",
    "HuggingFaceSource",
    "LMSource",
    "ModelPiece",
    "ModelSource",
    "SourceRegistry",
]
