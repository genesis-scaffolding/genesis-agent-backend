"""HuggingFace source package.

Auto-discovered by :class:`~genesis_worker.sources._registry.SourceRegistry`,
which imports this package and instantiates :class:`HuggingFaceSource`.
The class lives in :mod:`.source`; this ``__init__`` re-exports it so
``from genesis_worker.sources.huggingface import HuggingFaceSource`` works
the same way it did when the class lived in a single ``huggingface.py`` file.
"""

from .source import HuggingFaceSource

__all__ = ["HuggingFaceSource"]
