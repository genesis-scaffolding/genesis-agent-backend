"""Background session runtime — thread + cancel + log + terminal states.

Subclasses implement :meth:`view`, :meth:`submit`, and :meth:`_run_inner`,
and call :meth:`_start` when ready for the worker thread to begin.
The base owns the daemon thread, the cancellation event, the log
tail, and the translation of ``_Canceled`` and any other exception
to terminal :class:`AcquireStateKind` values. See ADR-027.
"""

from __future__ import annotations

import threading
from abc import abstractmethod

from ..contracts import (
    AcquireChoice,
    AcquireSession,
    AcquireState,
    AcquireStateKind,
    AcquireView,
)


class _Canceled(Exception):
    """Raised inside ``_run_inner`` to signal cancellation.

    The thread supervisor catches it and transitions state to ``CANCELLED``.
    Defined here so the exception lives next to the runtime that raises
    and catches it.
    """


_INTERACTIVE_KINDS = frozenset(
    {
        AcquireStateKind.INSPECTING,
        AcquireStateKind.SELECTING,
        AcquireStateKind.CONFIRMING,
    }
)


class BackgroundSession(AcquireSession):
    """Base class for long-running sessions with thread + cancel + log.

    Subclasses call :meth:`_start` when ready for the worker thread to
    run :meth:`_run_inner`. Eager sessions (e.g. install pipelines)
    call it in ``__init__``; lazy sessions (e.g. HF acquire, where the
    user must confirm before the download begins) call it after the
    user has approved the action.
    """

    def __init__(self, state: AcquireState) -> None:
        self._state = state
        self._cancel = threading.Event()
        self._log_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> AcquireState:
        return self._state

    @property
    def _cancel_event(self) -> threading.Event:
        return self._cancel

    def cancel(self) -> None:
        """Request cancellation. Idempotent.

        If the worker thread has not started yet (lazy sessions, pre-confirm),
        transition to ``CANCELLED`` synchronously — no thread is running to
        observe the event. Otherwise just set the event and let the worker
        thread observe it between long-running steps.
        """
        self._cancel.set()
        if self._thread is None and self._state.kind in _INTERACTIVE_KINDS:
            self._state.kind = AcquireStateKind.CANCELLED

    def wait(self) -> AcquireView:
        """Block until the worker thread completes, then return the final view.

        No-op if the thread was never started (interactive wizard still
        waiting for user input).
        """
        if self._thread is not None:
            self._thread.join()
        return self.view()

    def _start(self) -> None:
        """Spawn the daemon thread that runs :meth:`_run_inner`."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Thread supervisor. Translates exceptions to terminal kinds."""
        self._pre_run_hook()
        try:
            self._run_inner()
        except _Canceled:
            self._state.kind = AcquireStateKind.CANCELLED
        except Exception as exc:  # noqa: BLE001 — surface any failure
            self._state.kind = AcquireStateKind.FAILED
            self._state.failure = f"{type(exc).__name__}: {exc}"
        self._post_run_hook()

    def _append_log(self, line: str) -> None:
        """Append a line to the session's log tail (thread-safe, last 200).

        Writes to ``state.log_tail`` — the same list the view reads — so
        UI consumers see what the worker emits.
        """
        with self._log_lock:
            self._state.log_tail.append(line)
            if len(self._state.log_tail) > 200:
                del self._state.log_tail[: len(self._state.log_tail) - 200]

    @abstractmethod
    def view(self) -> AcquireView: ...

    @abstractmethod
    def submit(self, choice: AcquireChoice) -> None: ...

    @abstractmethod
    def _run_inner(self) -> None:
        """Subclass's actual work. Runs in the worker thread.

        Check ``self._cancel_event.is_set()`` between long-running steps.
        On success: set ``self._state.kind = AcquireStateKind.COMPLETE``.
        On failure: raise any exception (caught by the supervisor).
        On cancellation: raise ``_Canceled``.
        """

    def _pre_run_hook(self) -> None:
        """Run before _run_inner. Override in subclass. No-op by default."""

    def _post_run_hook(self) -> None:
        """Run after _run_inner. Override in subclass. No-op by default."""


__all__ = ["BackgroundSession", "_Canceled"]
