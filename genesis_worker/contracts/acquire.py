"""Acquire flow — the state machine a session drives to fetch something.

The contract has three types: :class:`AcquireState` (workflow position),
:class:`AcquireView` (UI snapshot), and :class:`AcquireChoice` (user input).
Sessions extend these to add their own domain-specific data; the framework
only models the universal bits. See ADR-027.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class AcquireStateKind(StrEnum):
    """States a session can be in. Sessions visit only the kinds they need.

    Interactive pre-thread kinds (INSPECTING, SELECTING, CONFIRMING) expect
    the user to drive transitions via :meth:`AcquireSession.submit`. Pipeline
    kinds (FETCHING, VERIFYING, EXTRACTING) advance inside the worker
    thread. Terminal kinds (COMPLETE, FAILED, CANCELLED) end the session.
    """

    INSPECTING = "inspecting"
    SELECTING = "selecting"
    CONFIRMING = "confirming"
    FETCHING = "fetching"
    VERIFYING = "verifying"
    EXTRACTING = "extracting"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AcquireProgress:
    bytes_done: int
    bytes_total: int
    speed_bps: int
    eta_s: int


@dataclass
class AcquireState:
    """The state of one acquire session. Subclass for session-specific data."""

    kind: AcquireStateKind
    repo_id: str
    confirmed: bool = False
    bytes_done: int = 0
    bytes_total: int = 0
    log_tail: list[str] = field(default_factory=list)
    failure: str | None = None


@dataclass(frozen=True)
class AcquireView:
    """UI snapshot of one acquire step. Subclass for session-specific fields."""

    kind: AcquireStateKind
    title: str
    prompt: str | None = None
    progress: AcquireProgress | None = None
    log_tail: list[str] | None = None
    can_cancel: bool = True
    error: str | None = None
    cache_dir: Path | None = None
    total_bytes: int | None = None


@dataclass(frozen=True)
class AcquireChoice:
    """User input for one acquire step. Subclass for session-specific fields.

    An empty choice means "advance with no input" — useful for self-driven
    transitions inside the worker thread.
    """

    confirm: bool | None = None


class AcquireSession(ABC):
    """State-machine session for one acquisition target."""

    source_name: str

    @property
    @abstractmethod
    def repo_id(self) -> str: ...

    @property
    @abstractmethod
    def state(self) -> AcquireState: ...

    @abstractmethod
    def view(self) -> AcquireView: ...

    @abstractmethod
    def submit(self, choice: AcquireChoice) -> None: ...

    @abstractmethod
    def cancel(self) -> None: ...

    @abstractmethod
    def wait(self) -> AcquireView: ...


__all__ = [
    "AcquireChoice",
    "AcquireProgress",
    "AcquireSession",
    "AcquireState",
    "AcquireStateKind",
    "AcquireView",
]
