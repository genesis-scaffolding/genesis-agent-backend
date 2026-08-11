"""Inference service plugins. One subpackage per service; the registry discovers them."""

from .llama_swap import LlamaSwapService

__all__ = [
    "LlamaSwapService",
]
