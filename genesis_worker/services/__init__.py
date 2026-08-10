"""Inference service extension axis.

The :class:`ServiceRegistry` facade is the single point of construction
for inference services. On construction the registry auto-discovers
every subpackage under :mod:`genesis_worker.services`, imports each,
finds the concrete ``InferenceService`` class, and instantiates it
with its per-service settings slice.

Typical usage::

    from genesis_worker.services import ServiceRegistry
    from genesis_worker.settings import Settings

    registry = ServiceRegistry(Settings())
    for svc in registry.all():
        print(svc.name, svc.capabilities())

Adding a new service is one new subpackage under this directory; the
registry picks it up automatically. The :class:`InferenceService`
Protocol and result / status / capability dataclasses ship in
:mod:`genesis_worker.services._base`.
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
