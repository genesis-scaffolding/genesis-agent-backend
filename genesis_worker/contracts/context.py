"""Plugin construction contexts — what the framework resolves and hands to a plugin."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .host import HostInfo
from .secret import NoSecretsAccessor, SecretsAccessor


@dataclass(frozen=True)
class PluginContext:
    """Resolved locations and the plugin's own options slice.

    Directories are already scoped to the plugin (``<data_dir>/llama-swap``); the
    plugin creates them on demand. ``options`` is the raw settings slice — the
    framework carries it without interpreting it, so each plugin owns its schema.

    ``secrets`` is the framework's read-only accessor for tokens (e.g.
    ``github_token``). Plugins ask for a secret by name; they never reach
    into ``os.environ`` or ``.env`` (ADR-009). Defaults to
    :class:`NoSecretsAccessor` so callers that don't care can omit it.

    ``repo_root`` is transitional: it exists so recipes can name binaries relative
    to the checkout. It drops out when state moves to XDG dirs (ADR-008 phase 11).

    ``vault_path`` is the model vault root; lifted from SourceContext so service
    plugins can also see it (ADR-023).

    ``media_vault_path`` is on ``ServiceContext`` only — the root for content
    produced by services (ComfyUI inputs/outputs, pictures indexed by
    PhotoPrism, future media peers). It lives on the service context because
    no source walks user-produced content today; lifting it to
    ``PluginContext`` is a one-line ADR if a future source ever needs it
    (ADR-037).

    ``host_info`` is the framework-level snapshot of the host (hostname, OS,
    GPU vendors, NVIDIA driver/runtime). Defaults to :meth:`HostInfo.empty`
    so plugin authors who don't care about hardware see no churn; the
    framework populates the real snapshot at registry construction.
    """

    name: str
    data_dir: Path
    config_dir: Path
    cache_dir: Path
    state_dir: Path
    log_dir: Path
    repo_root: Path
    vault_path: Path
    host_info: HostInfo = field(default_factory=HostInfo.empty)
    secrets: SecretsAccessor = field(default_factory=NoSecretsAccessor)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceContext(PluginContext):
    local_path: Path = field(default_factory=Path)
    # vault_path inherited from PluginContext (ADR-023).


@dataclass(frozen=True)
class ServiceContext(PluginContext):
    media_vault_path: Path = field(default_factory=Path)


__all__ = ["PluginContext", "ServiceContext", "SourceContext"]
