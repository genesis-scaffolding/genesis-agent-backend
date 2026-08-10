"""LM Studio source package.

Auto-discovered by :class:`~genesis_worker.sources._registry.SourceRegistry`,
which imports this package and instantiates :class:`LMSource`.
The class lives in :mod:`.source`; this ``__init__`` re-exports it so
``from genesis_worker.sources.lmstudio import LMSource`` works the same way
it did when the class lived in a single ``lmstudio.py`` file.
"""

from .source import LMSource

__all__ = ["LMSource"]
