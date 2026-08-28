"""Acquire flow — the state machine a source drives to download a model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AcquireFileGroup:
    """One selectable file, or a group of shards for one selectable model."""

    paths: list[str]
    size: int | None  # total across the group; None if any shard's size is unknown
    role: str  # "main", "mmproj", "mtp", "unsupported"
    label: str
    is_sharded: bool


@dataclass(frozen=True)
class AcquireProgress:
    bytes_done: int
    bytes_total: int
    speed_bps: int
    eta_s: int


@dataclass(frozen=True)
class AcquireStep:
    """One state in the acquire flow."""

    kind: str  # inspecting | select_files | confirm_storage | downloading |
    # complete | failed | cancelled
    title: str
    prompt: str | None = None
    file_groups: list[AcquireFileGroup] | None = None
    total_bytes: int | None = None
    cache_dir: Path | None = None
    progress: AcquireProgress | None = None
    log_tail: list[str] | None = None
    can_cancel: bool = True
    error: str | None = None


@dataclass(frozen=True)
class AcquireChoice:
    """User input for one :class:`AcquireStep`."""

    main_indexes: list[int] | None = None
    aux_indexes: list[int] | None = None
    confirm: bool | None = None


class AcquireState:
    """Server-side state for one in-flight acquire session."""

    def __init__(self, source: str, repo_id: str) -> None:
        self.source = source
        self.repo_id = repo_id
        self.selected_main: list[AcquireFileGroup] = []
        self.selected_aux: list[AcquireFileGroup] = []
        self.confirmed: bool = False
        self.last_step: AcquireStep | None = None


class AcquireSession(ABC):
    """State-machine acquisition for one repo on one source.

    step = session.current_step(); step = session.submit(choice); session.cancel()
    """

    source_name: str

    @property
    @abstractmethod
    def repo_id(self) -> str: ...

    @abstractmethod
    def current_step(self) -> AcquireStep: ...

    @abstractmethod
    def submit(self, choice: AcquireChoice) -> AcquireStep: ...

    @abstractmethod
    def cancel(self) -> None: ...


__all__ = [
    "AcquireChoice",
    "AcquireFileGroup",
    "AcquireProgress",
    "AcquireSession",
    "AcquireState",
    "AcquireStep",
]
