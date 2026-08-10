"""Plugin construction contexts — what the framework resolves and hands to a plugin."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginContext:
    """Resolved locations and the plugin's own options slice.

    Directories are already scoped to the plugin (``<data_dir>/llama-swap``); the
    plugin creates them on demand. ``options`` is the raw settings slice — the
    framework carries it without interpreting it, so each plugin owns its schema.

    ``repo_root`` is transitional: it exists so recipes can name binaries relative
    to the checkout. It drops out when state moves to XDG dirs (ADR-008 phase 11).
    """

    name: str
    data_dir: Path
    config_dir: Path
    cache_dir: Path
    state_dir: Path
    log_dir: Path
    repo_root: Path
    options: Mapping[str, Any]


@dataclass(frozen=True)
class SourceContext(PluginContext):
    local_path: Path
    vault_path: Path


@dataclass(frozen=True)
class ServiceContext(PluginContext):
    pass


__all__ = ["PluginContext", "ServiceContext", "SourceContext"]
