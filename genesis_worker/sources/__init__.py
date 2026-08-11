"""Model source plugins. One subpackage per source; the registry discovers them."""

from .huggingface import HuggingFaceSource
from .lmstudio import LMSource

__all__ = [
    "HuggingFaceSource",
    "LMSource",
]
