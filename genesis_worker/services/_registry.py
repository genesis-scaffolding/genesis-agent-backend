"""Service registry.

Mirror of :mod:`genesis_worker.sources._registry`. Plan-001 uses only
the registration scaffolding; the full ``InferenceService`` protocol
lands in plan-002 along with the llama-swap implementation.
"""

from __future__ import annotations

import importlib
import pkgutil

# Loose typing for the same reason as the source registry: callers get
# concrete constructor signatures back when they instantiate.
_REGISTRY: dict[str, type] = {}


def register_service(cls: type) -> type:
    """Class decorator: register a service implementation under its name."""
    _REGISTRY[cls.name] = cls
    return cls


def all_services() -> list:
    """Return one instance of every registered service."""
    return [cls() for cls in _REGISTRY.values()]


def _bootstrap() -> None:
    """Import every sibling submodule so their ``@register_service`` runs."""
    pkg = __package__ or ""
    package = importlib.import_module(pkg)
    assert package.__path__ is not None
    for mod in pkgutil.iter_modules(package.__path__):
        name = mod.name
        if not name or name.startswith("_"):
            continue
        importlib.import_module(f"{pkg}.{name}")


_bootstrap()


__all__ = ["all_services", "register_service"]
