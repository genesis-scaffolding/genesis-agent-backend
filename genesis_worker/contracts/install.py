"""Service install ABC and types — plugins own acquisition (ADR-012, ADR-028).

The runtime is unified under :class:`AcquireSession` (see
``acquire.py``); :class:`ServiceInstall` is the plugin-side entry
point for "what's installed, what's available, how to install".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from .acquire import AcquireSession


class InstallState(StrEnum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"


class InstallVersion(NamedTuple):
    version: str
    url: str
    sha256: str | None
    size_bytes: int | None


class ServiceInstall(ABC):
    """One installable binary managed by a service plugin."""

    name: str

    @abstractmethod
    def state(self) -> InstallState: ...

    @abstractmethod
    def installed_version(self) -> str | None: ...

    @abstractmethod
    def available_versions(self) -> list[InstallVersion]: ...

    @abstractmethod
    def binary_path(self) -> Path | None: ...

    @abstractmethod
    def install(self, *, version: str | None = None) -> AcquireSession: ...

    @abstractmethod
    def uninstall(self, *, version: str | None = None) -> None: ...

    def source_url(self) -> str | None:
        """Return the upstream source/tracking URL for this binary, or None if unknown."""
        return None


__all__ = [
    "InstallState",
    "InstallVersion",
    "ServiceInstall",
]
