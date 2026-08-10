"""Inference service extension axis.

The :class:`ServiceRegistry` facade is the single point of construction
for inference services. Services receive their settings slice from the
registry and operate on it.

Typical usage::

    from genesis_worker.services import ServiceRegistry
    from genesis_worker.services.llama_swap import LlamaSwapService
    from genesis_worker.settings import Settings

    registry = ServiceRegistry(Settings(), [LlamaSwapService])

The :class:`InferenceService` Protocol itself ships in plan-002 along
with the llama-swap implementation. This scaffolding establishes the
extensibility contract (one class per module, passed explicitly to the
registry) before any service exists.

ADR-003 details the extension architecture.
"""

from ._registry import ServiceRegistry

__all__ = ["ServiceRegistry"]
