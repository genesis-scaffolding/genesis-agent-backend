"""Inference service extension axis.

The :class:`ServiceRegistry` facade is the single point of construction
for inference services. Concrete services implement the
:class:`InferenceService` Protocol declared in :mod:`genesis_worker.services._base`.

Typical usage::

    from genesis_worker.services import (
        ServiceRegistry,
        LlamaSwapService,
        ServiceCapabilities,
    )
    from genesis_worker.settings import Settings

    registry = ServiceRegistry(Settings(), [LlamaSwapService])
    svc = registry.get("llama_swap")
    caps = svc.capabilities()
    assert caps.can_serve_llm

Adding a new service is one new module + implementing the
:class:`InferenceService` Protocol + adding the class to the list
passed to :class:`ServiceRegistry`. ADR-003 details the extension
architecture.
"""

from ._base import (
    InferenceService,
    ServiceCapabilities,
    ServiceResourceEstimate,
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
)
from ._registry import ServiceRegistry

__all__ = [
    "InferenceService",
    "ServiceCapabilities",
    "ServiceRegistry",
    "ServiceResourceEstimate",
    "ServiceState",
    "ServiceStatus",
    "StartResult",
    "StopResult",
]
