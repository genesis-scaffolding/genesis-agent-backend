"""Framework-level view types returned by the facade to UI and CLI consumers.

``HostInfo`` and ``Hardware`` live in :mod:`genesis_worker.contracts.host`
because they cross the framework/plugin boundary via
:class:`PluginContext`; this module holds the framework-internal
view types only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts.service import ServiceCapabilities, ServiceCategory


@dataclass(frozen=True)
class SourceInfo:
    """Display view of one registered source."""

    name: str
    display_name: str
    can_acquire: bool
    is_available: bool


@dataclass(frozen=True)
class ServiceInfo:
    """Display view of one registered service."""

    name: str
    display_name: str
    capabilities: ServiceCapabilities
    category: ServiceCategory = ServiceCategory.OTHER
    description: str = ""


@dataclass(frozen=True)
class MachineMetrics:
    """Snapshot of system resource usage at one moment in time."""

    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    gpu_percent: float | None
    vram_used_gb: float | None
    vram_total_gb: float | None


# Where a knob's resolved value came from (ADR-034 — Settings page source badges).
SettingSource = str  # "default" | "dotenv" | "user_overrides" | "env" | "constructor"


@dataclass(frozen=True)
class SettingSnapshot:
    """One row of the Settings page's effective-settings table.

    ``name`` is the dotted settings key (``paths.vault_path``,
    ``sources.huggingface.local_path``). ``value`` is the resolved
    value the framework is actually using. ``source`` tells the UI
    which layer won the precedence race; the page renders a badge
    from it.
    """

    name: str
    value: Any
    source: SettingSource
    override_key: str  # the env-var form, used by the editor
    override_path: Path | None = None  # path field (None for scalars)


__all__ = [
    "MachineMetrics",
    "ServiceInfo",
    "SettingSnapshot",
    "SettingSource",
    "SourceInfo",
]
