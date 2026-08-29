"""Service install ABC and types — plugins own acquisition (ADR-012)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from .acquire import AcquireChoice, AcquireView


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
    def install(self, *, version: str | None = None) -> InstallSession: ...

    @abstractmethod
    def uninstall(self, *, version: str | None = None) -> None: ...

    def source_url(self) -> str | None:
        """Return the upstream source/tracking URL for this binary, or None if unknown."""
        return None


class InstallSession(ABC):
    """Streaming install state machine. Mirrors :class:`AcquireSession`.

    New ``AcquireView.kind`` values: ``fetching | verifying | extracting``.
    Terminals ``complete | failed | cancelled`` are reused.
    """

    @abstractmethod
    def current_step(self) -> AcquireView: ...

    @abstractmethod
    def submit(self, choice: AcquireChoice) -> AcquireView: ...

    @abstractmethod
    def cancel(self) -> None: ...

    @abstractmethod
    def wait(self) -> AcquireView: ...


__all__ = [
    "InstallSession",
    "InstallState",
    "InstallVersion",
    "ServiceInstall",
]
