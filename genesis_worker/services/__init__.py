"""Inference service extension axis.

Re-exports the registry helpers for ergonomic imports:
    from genesis_worker.services import all_services, register_service

The ``InferenceService`` Protocol itself ships in plan-002 (it has too
many cross-references to the lifecycle / status / result dataclasses
to land piecemeal here).

ADR-003 details the extension architecture.
"""

from ._registry import all_services, register_service

__all__ = ["all_services", "register_service"]
