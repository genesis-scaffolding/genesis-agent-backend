"""Framework-level view types returned by the facade to UI and CLI consumers."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ServiceCapabilities


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


@dataclass(frozen=True)
class HostInfo:
    """Display view of the host this worker is running on."""

    hostname: str
    os: str            # e.g., "Linux 6.5.0-arch1-1"
    arch: str          # e.g., "x86_64"
    python: str        # e.g., "3.11.7"
    uptime_s: int | None
    public_ip: str | None
    tailscale_ip: str | None


__all__ = [
    "HostInfo",
    "ServiceInfo",
    "SourceInfo",
]
