"""Model source extension axis — the :class:`ModelSource` Protocol and acquire flow types."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .catalog import DiscoveredModel


@runtime_checkable
class ModelSource(Protocol):
    """One kind of model repository.

    Concrete sources declare:

    - ``name``: short identifier (``"huggingface"``, ``"lmstudio"``).
    - ``display_name``: human-readable name for UI.
    - ``can_acquire``: whether :class:`AcquireSession` is implemented
      (ships in spec-002).
    - ``vault_subdir``: subdirectory under ``vault_path`` where this
      source's models live (``"huggingface/hub"``,
      ``"lmstudio/models"``). The framework uses this to default
      ``local_path`` when settings don't override it.
    - ``local_path``: the resolved path the framework assigned at
      construction. Sources do not compute this themselves.

    The framework constructs each source with ``local_path=<resolved>``
    at registry-init time (see :class:`SourceRegistry`).
    """

    name: str
    display_name: str
    can_acquire: bool
    vault_subdir: str
    local_path: Path

    def is_available(self) -> bool: ...
    def walk(self) -> Sequence[DiscoveredModel]: ...


# ---------------------------------------------------------------------------
# Acquire flow types (spec-002). A source's acquisition is a state
# machine, not a script: each source ships an AcquireSession whose
# current_step() / submit(choice) / cancel() drive a shared protocol.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquireFileGroup:
    """One selectable file, or a group of shards for one selectable model."""

    paths: list[str]
    size: int | None  # total bytes across the group (None if any shard's size is unknown)
    role: str  # "main", "mmproj", "mtp", "unsupported"
    label: str  # human-readable: filename, or "<base.gguf> (N shards)"
    is_sharded: bool


@dataclass(frozen=True)
class AcquireProgress:
    """Download progress snapshot."""

    bytes_done: int
    bytes_total: int
    speed_bps: int
    eta_s: int


@dataclass(frozen=True)
class AcquireStep:
    """One state in the acquire flow."""

    kind: str  # "inspecting" | "select_files" | "confirm_storage" |
    # "downloading" | "complete" | "failed" | "cancelled"
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

    main_index: int | None = None
    aux_indexes: list[int] | None = None
    confirm: bool | None = None


class AcquireState:
    """Server-side state for one in-flight acquire session."""

    def __init__(self, source: str, repo_id: str) -> None:
        self.source = source
        self.repo_id = repo_id
        self.selected_main: AcquireFileGroup | None = None
        self.selected_aux: list[AcquireFileGroup] = []
        self.confirmed: bool = False
        self.last_step: AcquireStep | None = None


@runtime_checkable
class AcquireSession(Protocol):
    """State-machine-driven acquisition for one repo on one source.

    Lifecycle (UI / CLI):
        step = session.current_step()  # initial inspecting step
        # UI renders the step; user submits a choice
        session.submit(AcquireChoice(...))
        step = session.current_step()  # next step
        # ...
        session.cancel()  # at any point the user can cancel
    """

    source_name: str
    repo_id: str

    def current_step(self) -> AcquireStep: ...
    def submit(self, choice: AcquireChoice) -> AcquireStep: ...
    def cancel(self) -> None: ...


__all__ = [
    "AcquireChoice",
    "AcquireFileGroup",
    "AcquireProgress",
    "AcquireState",
    "AcquireStep",
    "ModelSource",
]
