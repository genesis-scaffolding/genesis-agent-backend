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


__all__ = [
    "ServiceInfo",
    "SourceInfo",
]
