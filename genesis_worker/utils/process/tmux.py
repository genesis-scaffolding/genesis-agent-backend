"""Tmux session lifecycle — start, stop, interrupt, and probe a named session."""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ...contracts import StartResult, StopResult

_DEFAULT_CHILD_WAIT_TIMEOUT_S = 30.0
_CHILD_POLL_INTERVAL_S = 0.5


class TmuxProcess:
    """Stateless tmux session management.

    ``start()`` and ``stop()`` compose into the service lifecycle:
    ``start()`` launches a detached session and waits for readiness;
    ``stop()`` sends SIGINT, waits for children to drain, and tears
    down the session.
    """

    def __init__(self, session_name: str) -> None:
        self._session_name = session_name

    @staticmethod
    def has_session(name: str) -> bool:
        """True iff a tmux session named ``name`` exists."""
        return (
            subprocess.run(
                ["tmux", "has-session", "-t", name],
                check=False,
                capture_output=True,
            ).returncode
            == 0
        )

    def exists(self) -> bool:
        """True iff this instance's session exists."""
        return self.has_session(self._session_name)

    def kill(self) -> None:
        """Kill the tmux session if it exists. No-op if not running."""
        if self.exists():
            subprocess.run(
                ["tmux", "kill-session", "-t", self._session_name],
                check=False,
                capture_output=True,
            )

    def send_interrupt(self) -> None:
        """Send Ctrl-C to the foreground process group in the session.

        tmux routes the keypress to the pane's active process. For a
        pipeline (bash running ``binary ... | tee ...``), bash receives
        SIGINT and propagates it to its children.
        """
        subprocess.run(
            ["tmux", "send-keys", "-t", self._session_name, "C-c"],
            check=False,
            capture_output=True,
        )

    def start(
        self,
        cmd: str,
        log_file: Path,
        *,
        wait_for_children: Callable[[], bool] | None = None,
        child_wait_timeout_s: float = _DEFAULT_CHILD_WAIT_TIMEOUT_S,
    ) -> StartResult:
        """Start ``cmd`` in a new detached tmux session.

        The command is always wrapped as::

            {cmd} 2>&1 | tee -a {log_file}

        ``wait_for_children`` and ``child_wait_timeout_s`` are stored on
        the instance and used by :meth:`stop` to drain children gracefully
        when the session is torn down. They have no effect during start.

        ``log_file`` parent is created if absent.

        Returns ``StartResult`` with ``ok=False`` and a message on failure.
        """
        # Tear down any prior session of the same name.
        self.kill()

        log_file.parent.mkdir(parents=True, exist_ok=True)
        wrapped = f"{cmd} 2>&1 | tee -a {shlex.quote(str(log_file))}"
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", self._session_name, wrapped],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return StartResult(
                ok=False,
                message=f"tmux new-session failed: {result.stderr.strip() or 'unknown error'}",
            )

        # Store child-drain parameters for use in stop().
        self._wait_for_children = wait_for_children
        self._child_wait_timeout_s = child_wait_timeout_s

        return StartResult(ok=True, message=f"started {self._session_name}")

    def _graceful_shutdown(
        self,
        wait_for_children: Callable[[], bool],
        timeout_s: float,
    ) -> bool:
        """Send C-c, poll children, fall back to SIGKILL."""
        self.send_interrupt()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if wait_for_children():
                return True
            time.sleep(_CHILD_POLL_INTERVAL_S)

        if wait_for_children():
            return True

        # Hard kill remaining children.
        self._kill_children()
        return False

    def _kill_children(self) -> None:
        """Subclasses or callers override to target specific children."""

    def stop(self) -> StopResult:
        """Stop the session gracefully.

        If ``wait_for_children`` was passed to :meth:`start`, sends Ctrl-C
        and polls the predicate for up to the stored timeout before
        force-killing remaining children. Always tears down the tmux session.

        Returns ``StopResult``.
        """
        if not self.exists():
            return StopResult(ok=True, message="no session")

        wait_for_children = getattr(self, "_wait_for_children", None)
        child_wait_timeout_s = getattr(self, "_child_wait_timeout_s", _DEFAULT_CHILD_WAIT_TIMEOUT_S)

        graceful = True
        if wait_for_children is not None:
            graceful = self._graceful_shutdown(wait_for_children, child_wait_timeout_s)
        else:
            self.send_interrupt()
            time.sleep(_CHILD_POLL_INTERVAL_S)

        if not graceful:
            self._kill_children()

        if self.exists():
            result = subprocess.run(
                ["tmux", "kill-session", "-t", self._session_name],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return StopResult(
                    ok=False,
                    message=f"tmux kill-session failed: {result.stderr.strip() or 'unknown error'}",
                )

        msg = f"killed {self._session_name}"
        if not graceful:
            msg += " (forced child cleanup)"
        return StopResult(ok=True, message=msg)


__all__ = ["TmuxProcess"]
