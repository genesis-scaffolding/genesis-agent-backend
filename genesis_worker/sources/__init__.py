"""Model source extension axis.

The :class:`SourceRegistry` facade is the single point of construction
for :class:`ModelSource` instances. On construction the registry
auto-discovers every subpackage under :mod:`genesis_worker.sources`,
imports each, finds the concrete ``ModelSource`` class, and instantiates
it with a resolved ``local_path``. Sources are pure logic — the
framework sets their path and they walk it.

Typical usage::

    from genesis_worker.sources import SourceRegistry
    from genesis_worker.settings import Settings

    registry = SourceRegistry(Settings())
    for source in registry.all():
        if source.is_available():
            for model in source.walk():
                ...

Adding a new source is one new subpackage under this directory; the
registry picks it up automatically. ADR-003 details the extension
architecture.
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
