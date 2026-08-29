"""HuggingFace source package.

Auto-discovered by :class:`~genesis_worker.sources._registry.SourceRegistry`,
which imports this package and instantiates :class:`HuggingFaceSource`.
The class lives in :mod:`.source`; this ``__init__`` re-exports it so
``from genesis_worker.sources.huggingface import HuggingFaceSource`` works
the same way it did when the class lived in a single ``huggingface.py`` file.

The :class:`HfAcquireSession` lives in :mod:`.acquire` and is re-exported
here so callers can ``from genesis_worker.sources.huggingface import
HfAcquireSession``. The source itself (the walker) doesn't carry the
acquire session — the facade constructs the session on demand when
``start_acquire()`` is called.
"""

from .acquire import (
    HfAcquireChoice,
    HfAcquireSession,
    HfAcquireState,
    HfAcquireView,
    classify_path,
    group_files,
)
from .source import HuggingFaceSource

__all__ = [
    "HfAcquireChoice",
    "HfAcquireSession",
    "HfAcquireState",
    "HfAcquireView",
    "HuggingFaceSource",
    "classify_path",
    "group_files",
]
