"""Framework-level view types returned by the facade to UI and CLI consumers.

``HostInfo`` and ``Hardware`` live in :mod:`genesis_worker.contracts.host`
because they cross the framework/plugin boundary via
:class:`PluginContext`; this module holds the framework-internal
view types only.
"""

from __future__ import annotations

from dataclasses import dataclass

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


__all__ = [
    "MachineMetrics",
    "ServiceInfo",
    "SourceInfo",
]
