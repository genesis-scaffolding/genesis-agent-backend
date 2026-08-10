"""Service registry — facade over :class:`InferenceService` construction.

The :class:`ServiceRegistry` is the single point of construction for
inference services. On construction it walks the sibling subpackages
of ``genesis_worker.services``, imports each, finds the concrete
:class:`InferenceService` class, and instantiates it with its
per-service settings slice.

Each service lives in its own subpackage (``services/llama_swap/``)
with the class in ``service.py``. The package's ``__init__.py``
re-exports the class. Adding a new service is one new subpackage —
the registry picks it up automatically.

The :class:`InferenceService` Protocol and the result / status /
capability dataclasses ship in :mod:`genesis_worker.services._base`.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..settings import Settings


def _find_extension_class(module, *required_attrs: str) -> type | None:
    """Find the first class in ``module`` that declares every required attribute.

    Used to locate the concrete service class after auto-discovering a
    subpackage. Returns ``None`` if no such class is found.
    """
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if not isinstance(attr, type):
            continue
        if not all(hasattr(attr, a) for a in required_attrs):
            continue
        return attr
    return None


class ServiceRegistry:
    """Facade for constructing and looking up service instances.

    On construction, the registry walks the sibling subpackages of
    ``genesis_worker.services`` and instantiates one of every concrete
    service found. Each service is constructed with its per-service
    settings slice (e.g. ``settings.services.llama_swap``) as the
    ``settings`` kwarg. Services whose ``name`` does not appear under
    ``settings.services`` get ``settings=None``.

    Example::

        registry = ServiceRegistry(Settings())
        for svc in registry.all():
            print(svc.name, svc.capabilities())
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, Any] = {}
        self._discover()

    def _discover(self) -> None:
        """Walk sibling subpackages of ``genesis_worker.services``.

        Skips packages whose name starts with ``_`` (private helpers,
        protocols, this registry itself). For each remaining subpackage,
        imports it (triggering its ``__init__`` re-export) and looks for
        a class declaring both ``name`` and ``display_name``.
        """
        pkg = importlib.import_module(__package__ or "")
        assert pkg.__path__ is not None
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            name = mod_info.name
            if not name or name.startswith("_"):
                continue
            sub = importlib.import_module(f"{pkg.__name__}.{name}")
            cls = _find_extension_class(sub, "name", "display_name")
            if cls is None:
                continue
            per_service = getattr(self._settings.services, cls.name, None)
            self._instances[cls.name] = cls(settings=per_service)

    def get(self, name: str) -> Any:
        """Look up one service by its ``name`` attribute. Raises ``KeyError`` if unknown."""
        return self._instances[name]

    def all(self) -> list:
        """Return every registered service as a list, in registration order."""
        return list(self._instances.values())


__all__ = ["ServiceRegistry"]
