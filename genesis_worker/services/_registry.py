"""Service registry — facade over service construction.

The :class:`ServiceRegistry` is the single point of construction for
inference services. It takes a list of service *classes* (no decorator,
no auto-discovery) and a :class:`~genesis_worker.settings.Settings`
object, constructs each service with its per-service settings slice,
and exposes ``.get(name)`` / ``.all()``.

The :class:`InferenceService` Protocol itself ships in plan-002 along
with ``LlamaSwapService``. This facade is the plan-001 scaffolding —
it exists so plan-002 has a single point of construction, and so the
extensibility contract (one class per module, passed explicitly to the
registry) is established before any service exists.

ADR-003 details the extension architecture.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..settings import Settings


class ServiceRegistry:
    """Facade for constructing and looking up service instances.

    Construction is explicit: callers pass the service classes they want
    registered. No decorators, no module-level state, no auto-discovery.

    Each service is constructed with its per-service settings slice
    (e.g. ``settings.services.llama_swap``) as the ``settings`` kwarg.
    Services whose ``name`` does not appear under ``settings.services``
    get ``settings=None`` — they can opt to accept None or to require
    a settings slice.

    Example::

        registry = ServiceRegistry(Settings(), [LlamaSwapService])
        svc = registry.get("llama_swap")
    """

    def __init__(self, settings: Settings, service_classes: Iterable[type]) -> None:
        self._settings = settings
        self._instances: dict[str, Any] = {}
        for cls in service_classes:
            per_service = getattr(settings.services, cls.name, None)
            self._instances[cls.name] = cls(settings=per_service)

    def get(self, name: str) -> Any:
        """Look up one service by its ``name`` attribute. Raises ``KeyError`` if unknown."""
        return self._instances[name]

    def all(self) -> list:
        """Return every registered service as a list, in registration order."""
        return list(self._instances.values())


__all__ = ["ServiceRegistry"]
