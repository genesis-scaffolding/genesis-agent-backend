"""tmux + curl lifecycle for llama-swap.

Lifts the semantics of ``bin/up`` into Python so the worker can start
and stop llama-swap without shelling out. The bash script remains the
source of truth for human-driven startup until Phase 10 retirement; this
module is the programmatic equivalent.

Behavior matches ``bin/up``:

- Tears down any existing tmux session of the same name.
- Kills stray ``llama-server`` so llama-swap owns the port.
- Spawns a new tmux session running
  ``llama-swap --config <cfg> -listen <addr> -watch-config`` with output
  piped to a log file via ``tee -a``.
- Polls ``http://<addr>/v1/models`` until it returns 200 or the timeout
  elapses.
- Returns a :class:`StartResult` / :class:`StopResult` so the caller
  can branch on success without parsing stdout.

The running llama-swap on ``:8080`` (started via ``bin/up``) is not
touched by these helpers. Tests validate against a parallel instance on
a different port.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .._base import ServiceState, ServiceStatus, StartResult, StopResult

_DEFAULT_HEALTH_POLL_S = 1.0
_DEFAULT_HEALTH_TIMEOUT_S = 60.0


def start_swap(
    config: Path,
    listen_addr: str,
    session_name: str,
    log_file: Path,
    health_timeout_s: float = _DEFAULT_HEALTH_TIMEOUT_S,
) -> StartResult:
    """Start llama-swap in a tmux session and wait for it to be ready.

    Returns :class:`StartResult` with ``ok=False`` and a human-readable
    message on any failure mode (binary missing, config missing, did
    not become ready in time).
    """
    if shutil.which("llama-swap") is None:
        return StartResult(ok=False, message="llama-swap not on PATH")
    if not config.is_file():
        return StartResult(ok=False, message=f"config not found: {config}")

    # Tear down any prior session of the same name.
    if _has_session(session_name):
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=False,
            capture_output=True,
        )

    # Drop any stray llama-server so llama-swap can own the port.
    subprocess.run(
        ["pkill", "-9", "-f", "llama-server"],
        check=False,
        capture_output=True,
    )
    # Give the OS a moment to actually release the port.
    time.sleep(1)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = (
        f"llama-swap --config {config} -listen {listen_addr} "
        f"-watch-config 2>&1 | tee -a {log_file}"
    )
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, cmd],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return StartResult(
            ok=False,
            message=f"tmux new-session failed: {result.stderr.strip() or 'unknown error'}",
        )

    if wait_ready(listen_addr, health_timeout_s):
        return StartResult(ok=True, message=f"started {session_name}")
    return StartResult(
        ok=False,
        message=f"did not become ready in {health_timeout_s:.0f}s; see {log_file}",
    )


def stop_swap(session_name: str) -> StopResult:
    """Stop the llama-swap tmux session if it exists.

    Idempotent: returns ``ok=True`` with ``message='no session'`` when
    there is nothing to stop. ``bin/up`` behaves the same way.
    """
    if not _has_session(session_name):
        return StopResult(ok=True, message="no session")
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return StopResult(
            ok=False,
            message=f"tmux kill-session failed: {result.stderr.strip() or 'unknown error'}",
        )
    return StopResult(ok=True, message=f"killed {session_name}")


def is_running(session_name: str) -> bool:
    """True iff the named tmux session exists."""
    return _has_session(session_name)


def status(session_name: str, listen_addr: str) -> ServiceStatus:
    """Coarse status: session presence + a /v1/models probe.

    - session absent → ``STOPPED``
    - session present, /v1/models returns 200 → ``RUNNING`` with endpoint
    - session present, probe fails or returns non-200 → ``STARTING``
    """
    endpoint = f"http://{listen_addr}/v1"
    if not _has_session(session_name):
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    if _probe_models(listen_addr):
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)


def wait_ready(listen_addr: str, timeout_s: float) -> bool:
    """Poll ``/v1/models`` until it returns 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _probe_models(listen_addr):
            return True
        time.sleep(_DEFAULT_HEALTH_POLL_S)
    return False


def _has_session(name: str) -> bool:
    """True iff tmux reports a session of this name."""
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", name],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _probe_models(listen_addr: str) -> bool:
    """Single /v1/models probe. Returns True iff 200."""
    try:
        with urllib.request.urlopen(
            f"http://{listen_addr}/v1/models", timeout=1
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


__all__ = [
    "is_running",
    "start_swap",
    "status",
    "stop_swap",
    "wait_ready",
]