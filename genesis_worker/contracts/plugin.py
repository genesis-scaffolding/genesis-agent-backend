"""Common base for both plugin axes."""

from __future__ import annotations

from abc import ABC

from .context import PluginContext


class Plugin(ABC):
    """Name, display name, and the context the framework constructed it with.

    ``dir_name`` is the per-plugin directory segment the framework scopes paths by;
    it defaults to ``name`` with underscores hyphenated (``llama_swap`` →
    ``llama-swap``).
    """

    name: str
    display_name: str
    dir_name: str

    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "name", None)
        if name and "dir_name" not in cls.__dict__:
            cls.dir_name = name.replace("_", "-")


__all__ = ["Plugin"]
