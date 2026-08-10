"""Source registry.

Auto-discovers every module under :mod:`genesis_worker.sources` and
collects the classes decorated with :func:`register_source`. Each
module's import-time registration is what makes the framework see it.

ADR-003 details the extension architecture. Adding a new source is one
new file plus one ``@register_source`` decorator.
"""

from __future__ import annotations

import importlib
import pkgutil

from ._base import ModelSource

# We type the registry loosely as ``dict[str, type]`` rather than
# ``dict[str, type[ModelSource]]`` so callers (including ``cls()`` below
# and test code that constructs sources with kwargs) get the concrete
# constructor signature back. Type-checking the *contents* against the
# Protocol happens when consumers iterate ``all_sources()``.
_REGISTRY: dict[str, type] = {}


def register_source(cls: type) -> type:
    """Class decorator: register a ModelSource implementation under its name."""
    _REGISTRY[cls.name] = cls
    return cls


def all_sources() -> list[ModelSource]:
    """Return one instance of every registered source."""
    return [cls() for cls in _REGISTRY.values()]


def _bootstrap() -> None:
    """Import every sibling submodule so their ``@register_source`` runs."""
    pkg = __package__ or ""
    package = importlib.import_module(pkg)
    assert package.__path__ is not None
    for mod in pkgutil.iter_modules(package.__path__):
        name = mod.name
        if not name or name.startswith("_"):
            continue
        importlib.import_module(f"{pkg}.{name}")


_bootstrap()


__all__ = ["all_sources", "register_source"]
