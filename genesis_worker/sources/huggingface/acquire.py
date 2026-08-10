"""HuggingFace acquire session — state-machine-driven download wizard."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import HfApi

from ...contracts import (
    AcquireChoice,
    AcquireFileGroup,
    AcquireProgress,
    AcquireSession,
    AcquireState,
    AcquireStep,
)

GGUF_EXT = ".gguf"

# Mirror bin/hf-model.py: sharded GGUF naming pattern.
SHARD_RE = re.compile(
    r"^(?P<base>.+)-(?P<number>\d{5})-of-(?P<count>\d{5})(?P<ext>\.gguf)$",
    re.IGNORECASE,
)

# Role classification: same markers bin/hf-model.py used.
AUX_MARKERS = (
    "mmproj",
    "adapter",
    "lora",
    "controlnet",
    "text_encoder",
    "image_encoder",
    "vision_encoder",
    "vae",
    "draft",
)

_ROLE_ORDER = {"main": 0, "mmproj": 1, "mtp": 2, "unsupported": 3}


@dataclass(frozen=True)
class _RemoteFile:
    """Internal mirror of ``huggingface_hub.RepoFile``.

    The library's RepoFile takes ``oid`` as a required kwarg in
    ``__init__`` (its constructor is not part of the public surface).
    We use this lighter dataclass instead so the module is testable
    without constructing library internals.
    """

    path: str
    size: int | None


def classify_path(path: str) -> str:
    """Classify a remote GGUF path for the file-selection UI."""
    name = Path(path).name.lower()
    if "mmproj" in name:
        return "mmproj"
    if name.startswith("mtp-"):
        return "mtp"
    if any(marker in name for marker in AUX_MARKERS):
        return "unsupported"
    return "main"


def group_files(files: list[_RemoteFile]) -> list[AcquireFileGroup]:
    """Group split GGUF files by their base filename.

    Returns ``AcquireFileGroup`` objects in role-then-label order.
    Mirrors ``bin/hf-model.py:group_files``.
    """
    grouped: dict[str, list[_RemoteFile]] = {}
    for item in files:
        match = SHARD_RE.match(item.path)
        key = match.group("base") + match.group("ext") if match else item.path
        grouped.setdefault(key, []).append(item)

    groups: list[AcquireFileGroup] = []
    for key, members in grouped.items():
        members.sort(key=lambda item: item.path.lower())
        role = classify_path(members[0].path)
        size = (
            sum(item.size for item in members if item.size is not None)
            if all(item.size is not None for item in members)
            else None
        )
        if len(members) == 1:
            label = members[0].path
        else:
            label = f"{key} ({len(members)} shards)"
        groups.append(
            AcquireFileGroup(
                paths=[item.path for item in members],
                size=size,
                role=role,
                label=label,
                is_sharded=len(members) > 1,
            )
        )

    return sorted(groups, key=lambda g: (_ROLE_ORDER.get(g.role, 9), g.label.lower()))


class HfAcquireSession(AcquireSession):
    """State-machine acquire session for one Hugging Face repo.

    Construction:
        api       = HfApi() (or a mock in tests)
        state     = AcquireState(source='huggingface', repo_id='org/name')
        cache_dir = Path to the HF cache root
        revision  = branch / commit to inspect (default 'main')
        hf_hub_download = per-file download callable (default
                          ``huggingface_hub.hf_hub_download``; tests
                          inject a stub).
    """

    source_name = "huggingface"

    def __init__(
        self,
        api: HfApi,
        state: AcquireState,
        cache_dir: Path,
        *,
        revision: str = "main",
        hf_hub_download: Callable[..., str] | None = None,
    ) -> None:
        self._api = api
        self._state = state
        self._cache_dir = cache_dir
        self._revision = revision
        self._hf_hub_download = hf_hub_download
        # Cancellation; set by cancel(), checked between file downloads.
        self._cancel = threading.Event()
        # Inspection results, populated by _inspect().
        self._groups: list[AcquireFileGroup] = []
        self._files: list[_RemoteFile] = []
        self._error: str | None = None
        # Download thread (set when transition -> downloading).
        self._download_thread: threading.Thread | None = None
        # Logger tail captured during the download.
        self._log_tail: list[str] = []
        self._log_lock = threading.Lock()

    # --- Properties --------------------------------------------------------

    @property
    def repo_id(self) -> str:
        return self._state.repo_id

    @property
    def cancel_event(self) -> threading.Event:
        """The cancel event; exposed for tests that drive the thread directly."""
        return self._cancel

    # --- AcquireSession Protocol ------------------------------------------

    def current_step(self) -> AcquireStep:
        """Return the current step. Triggers inspection on first call.

        The inspection is synchronous so that the very first
        ``current_step()`` returns either ``select_files`` (with the
        file groups populated) or ``failed`` (if the API call raised).
        """
        if self._state.last_step is None:
            self._state.last_step = AcquireStep(
                kind="inspecting",
                title=f"Inspecting {self._state.repo_id}",
            )
            # Run inspection synchronously so file_groups are ready
            # by the time the UI fetches the next step.
            self._inspect()

        step = self._state.last_step
        assert step is not None  # for type checkers
        # While downloading, refresh progress into the live step.
        if step.kind == "downloading":
            return self._progress_step()
        return step

    def submit(self, choice: AcquireChoice) -> AcquireStep:
        """Advance the state machine based on the user's choice.

        Step -> next-step transitions:

        - inspecting (auto-resolves to select_files on first current_step)
        - select_files -> confirm_storage (validates main + aux indexes)
        - confirm_storage + confirm=True -> downloading (spawns thread)
        - terminal states (complete/failed/cancelled): no-op
        """
        step = self._state.last_step
        if step is None:
            # First call before any current_step() — same as inspecting.
            self.current_step()
            step = self._state.last_step

        if step is None:
            return AcquireStep(
                kind="failed",
                title="Acquire failed",
                error="internal: no current step after inspection",
                can_cancel=False,
            )

        if step.kind == "inspecting":
            # Shouldn't normally be reachable: current_step() already
            # transitioned past inspecting. Defensive fallback.
            self._inspect()
            return self._state.last_step  # type: ignore[return-value]

        if step.kind == "select_files":
            return self._submit_select_files(choice)
        if step.kind == "confirm_storage":
            return self._submit_confirm_storage(choice)
        # Terminal or in-flight: no transitions from submit.
        return step

    def cancel(self) -> None:
        """Request cancellation. The download thread aborts between files."""
        self._cancel.set()
        # If we're in a non-download step, transition to cancelled
        # immediately so the UI sees the right state.
        step = self._state.last_step
        if step is not None and step.kind in {"inspecting", "select_files", "confirm_storage"}:
            self._state.last_step = AcquireStep(
                kind="cancelled",
                title="Cancelled",
                can_cancel=False,
            )
        # Otherwise the download thread will pick up the event and
        # transition to cancelled itself.

    # --- Inspection --------------------------------------------------------

    def _inspect(self) -> None:
        """Walk the repo tree; build file groups; transition to select_files."""
        try:
            tree = self._api.list_repo_tree(
                self._state.repo_id,
                repo_type="model",
                revision=self._revision,
                recursive=True,
            )
        except Exception as exc:  # noqa: BLE001 (HF raises many error types)
            self._error = f"inspect failed: {exc}"
            self._state.last_step = AcquireStep(
                kind="failed",
                title=f"Failed to inspect {self._state.repo_id}",
                error=self._error,
                can_cancel=False,
            )
            return

        files: list[_RemoteFile] = []
        for item in tree:
            path = getattr(item, "path", None)
            if not path or not path.lower().endswith(GGUF_EXT):
                continue
            # Folder entries have no size; the GGUF filter makes these
            # impossible, but the guard keeps a future API shape from
            # turning a folder into a downloadable file.
            if not hasattr(item, "size"):
                continue
            size = getattr(item, "size", None)
            files.append(_RemoteFile(path=path, size=size))

        self._files = sorted(files, key=lambda f: f.path.lower())
        self._groups = group_files(self._files)

        if not any(g.role == "main" for g in self._groups):
            self._state.last_step = AcquireStep(
                kind="failed",
                title=f"No main GGUF in {self._state.repo_id}",
                error="repository has no main GGUF candidates",
                can_cancel=False,
            )
            return

        self._state.last_step = AcquireStep(
            kind="select_files",
            title=f"Select files for {self._state.repo_id}",
            prompt="Pick one main file and any auxiliaries",
            file_groups=self._groups,
            can_cancel=True,
        )

    # --- select_files -> confirm_storage ----------------------------------

    def _submit_select_files(self, choice: AcquireChoice) -> AcquireStep:
        if choice.main_index is None or choice.main_index < 1:
            self._state.last_step = self._select_files_step_with_error(
                "main_index is required",
            )
            return self._state.last_step  # type: ignore[return-value]

        if choice.main_index > len(self._groups):
            self._state.last_step = self._select_files_step_with_error(
                f"main_index {choice.main_index} out of range",
            )
            return self._state.last_step  # type: ignore[return-value]

        main = self._groups[choice.main_index - 1]
        if main.role != "main":
            self._state.last_step = self._select_files_step_with_error(
                f"selected group is role={main.role!r}, must be 'main'",
            )
            return self._state.last_step  # type: ignore[return-value]

        aux: list[AcquireFileGroup] = []
        for idx in choice.aux_indexes or []:
            if idx < 1 or idx > len(self._groups):
                self._state.last_step = self._select_files_step_with_error(
                    f"aux index {idx} out of range",
                )
                return self._state.last_step  # type: ignore[return-value]
            aux.append(self._groups[idx - 1])

        # Enforce single-mmproj and single-mtp (matches bin/hf-model.py).
        if sum(g.role == "mmproj" for g in aux) > 1:
            self._state.last_step = self._select_files_step_with_error(
                "select at most one mmproj",
            )
            return self._state.last_step  # type: ignore[return-value]
        if sum(g.role == "mtp" for g in aux) > 1:
            self._state.last_step = self._select_files_step_with_error(
                "select at most one MTP/draft",
            )
            return self._state.last_step  # type: ignore[return-value]

        self._state.selected_main = main
        self._state.selected_aux = aux

        total_bytes: int | None = None
        sizes: list[int | None] = [main.size, *(g.size for g in aux)]
        if all(s is not None for s in sizes):
            total_bytes = sum(s for s in sizes if s is not None)  # type: ignore[union-attr]

        self._state.last_step = AcquireStep(
            kind="confirm_storage",
            title=f"Confirm download for {self._state.repo_id}",
            prompt=f"Will download {len(aux) + 1} group(s) ({total_bytes or '?'} bytes)",
            total_bytes=total_bytes,
            cache_dir=self._cache_dir,
            can_cancel=True,
        )
        return self._state.last_step  # type: ignore[return-value]

    def _select_files_step_with_error(self, error: str) -> AcquireStep:
        return AcquireStep(
            kind="select_files",
            title=f"Select files for {self._state.repo_id}",
            prompt="Pick one main file and any auxiliaries",
            file_groups=self._groups,
            error=error,
            can_cancel=True,
        )

    # --- confirm_storage -> downloading -----------------------------------

    def _submit_confirm_storage(self, choice: AcquireChoice) -> AcquireStep:
        if not choice.confirm:
            # User declined: send back to select_files.
            self._state.selected_main = None
            self._state.selected_aux = []
            self._state.last_step = AcquireStep(
                kind="select_files",
                title=f"Select files for {self._state.repo_id}",
                prompt="Pick one main file and any auxiliaries",
                file_groups=self._groups,
                can_cancel=True,
            )
            return self._state.last_step  # type: ignore[return-value]

        # Spawn the download thread.
        self._state.confirmed = True
        # self._state.last_step is still the confirm_storage step we
        # were called with; capture its total_bytes before we overwrite it.
        previous_step = self._state.last_step
        confirm_total = previous_step.total_bytes if previous_step else 0
        confirm_total = confirm_total or 0
        self._state.last_step = AcquireStep(
            kind="downloading",
            title=f"Downloading {self._state.repo_id}",
            progress=AcquireProgress(
                bytes_done=0,
                bytes_total=confirm_total,
                speed_bps=0,
                eta_s=0,
            ),
            cache_dir=self._cache_dir,
            log_tail=[],
            can_cancel=True,
        )
        self._download_thread = threading.Thread(
            target=self._download_worker,
            daemon=True,
        )
        self._download_thread.start()
        return self._state.last_step  # type: ignore[return-value]

    # --- download worker ---------------------------------------------------

    def _download_worker(self) -> None:
        """Background thread: download selected files one at a time."""
        from huggingface_hub import hf_hub_download

        download = self._hf_hub_download or hf_hub_download
        handler = _LogTailHandler(self._log_tail, self._log_lock)
        handler.setLevel(logging.INFO)
        logger = logging.getLogger("huggingface_hub")
        logger.addHandler(handler)
        try:
            main = self._state.selected_main
            if main is None:
                raise RuntimeError("internal: no selected_main when downloading")
            selected = [main, *self._state.selected_aux]
            done = 0
            total = sum(s.size for s in selected if s.size is not None)
            for group in selected:
                if self._cancel.is_set():
                    self._state.last_step = AcquireStep(
                        kind="cancelled",
                        title="Cancelled",
                        can_cancel=False,
                    )
                    return
                for path in group.paths:
                    if self._cancel.is_set():
                        self._state.last_step = AcquireStep(
                            kind="cancelled",
                            title="Cancelled",
                            can_cancel=False,
                        )
                        return
                    try:
                        download(
                            repo_id=self._state.repo_id,
                            filename=path,
                            cache_dir=str(self._cache_dir),
                            revision=self._revision,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._state.last_step = AcquireStep(
                            kind="failed",
                            title=f"Download failed: {path}",
                            error=f"{type(exc).__name__}: {exc}",
                            log_tail=list(self._log_tail),
                            can_cancel=False,
                        )
                        return
                    # Check cancel after each file returns so a cancel
                    # that arrived during the in-flight download aborts
                    # the rest of the run (the spec's threading.Event
                    # strategy).
                    if self._cancel.is_set():
                        self._state.last_step = AcquireStep(
                            kind="cancelled",
                            title="Cancelled",
                            can_cancel=False,
                        )
                        return
                    done += group.size or 0
                    self._state.last_step = AcquireStep(
                        kind="downloading",
                        title=f"Downloading {self._state.repo_id}",
                        progress=AcquireProgress(
                            bytes_done=done,
                            bytes_total=total,
                            speed_bps=0,
                            eta_s=(total - done) // max(done, 1) if done else 0,
                        ),
                        cache_dir=self._cache_dir,
                        log_tail=list(self._log_tail[-20:]),
                        can_cancel=True,
                    )
            self._state.last_step = AcquireStep(
                kind="complete",
                title=f"Downloaded {self._state.repo_id}",
                progress=AcquireProgress(
                    bytes_done=total,
                    bytes_total=total,
                    speed_bps=0,
                    eta_s=0,
                ),
                log_tail=list(self._log_tail[-20:]),
                can_cancel=False,
            )
        finally:
            logger.removeHandler(handler)

    def _progress_step(self) -> AcquireStep:
        """Refresh the live downloading step with current log tail.

        The download thread mutates ``state.last_step`` in place with
        new progress; here we just return a copy with the latest
        log_tail length preserved.
        """
        step = self._state.last_step
        assert step is not None
        with self._log_lock:
            tail = list(self._log_tail[-20:])
        return AcquireStep(
            kind=step.kind,
            title=step.title,
            progress=step.progress,
            cache_dir=step.cache_dir,
            log_tail=tail,
            can_cancel=step.can_cancel,
            error=step.error,
        )


class _LogTailHandler(logging.Handler):
    """Logging handler that captures the last N records into a shared list."""

    def __init__(self, sink: list[str], lock: threading.Lock, maxlen: int = 200) -> None:
        super().__init__()
        self._sink = sink
        self._lock = lock
        self._maxlen = maxlen

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        with self._lock:
            self._sink.append(msg)
            if len(self._sink) > self._maxlen:
                del self._sink[: len(self._sink) - self._maxlen]


__all__ = [
    "HfAcquireSession",
    "classify_path",
    "group_files",
]

