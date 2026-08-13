"""tmux lifecycle for cptr."""

from __future__ import annotations

import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ...contracts import ServiceState, ServiceStatus, StartResult, StopResult

_DEFAULT_HEALTH_POLL_S = 1.0
_DEFAULT_HEALTH_TIMEOUT_S = 60.0
_GRACEFUL_STOP_TIMEOUT_S = 10.0


def start_cptr(
    *,
    binary: Path,
    host: str,
    port: int,
    session_name: str,
    log_file: Path,
    health_timeout_s: float = _DEFAULT_HEALTH_TIMEOUT_S,
) -> StartResult:
    """Start cptr in a tmux session and wait for HTTP readiness.

    Returns ``StartResult`` with ``ok=False`` and a human-readable
    message on any failure mode (binary missing, tmux new-session
    failed, did not become ready in time).
    """
    if not binary.is_file():
        return StartResult(ok=False, message=f"binary not found: {binary}")

    if _has_session(session_name):
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=False,
            capture_output=True,
        )

    log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = (
        f"{shlex.quote(str(binary))} run "
        f"--host {shlex.quote(host)} --port {port} "
        f"2>&1 | tee -a {shlex.quote(str(log_file))}"
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

    probe_host = _probe_host(host)
    if wait_ready(probe_host, port, health_timeout_s):
        return StartResult(ok=True, message=f"started {session_name}")
    return StartResult(
        ok=False,
        message=f"did not become ready in {health_timeout_s:.0f}s; see {log_file}",
    )


def stop_cptr(session_name: str) -> StopResult:
    """Stop the cptr tmux session.

    Sends Ctrl-C to the foreground process (the bash pipeline running
    cptr), waits briefly, then hard-kills the session if it's still
    around. cptr has no children to drain, so this is faster than the
    llama-swap equivalent.
    """
    if not _has_session(session_name):
        return StopResult(ok=True, message="no session")

    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "C-c"],
        check=False,
        capture_output=True,
    )

    deadline = time.monotonic() + _GRACEFUL_STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not _has_session(session_name):
            return StopResult(ok=True, message=f"killed {session_name}")
        time.sleep(0.5)

    if _has_session(session_name):
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=False,
            capture_output=True,
            text=True,
        )
    return StopResult(ok=True, message=f"killed {session_name} (forced)")


def is_running(session_name: str) -> bool:
    """True iff the named tmux session exists."""
    return _has_session(session_name)


def status(session_name: str, host: str, port: int) -> ServiceStatus:
    """Coarse status: session presence + an HTTP root probe."""
    endpoint = f"http://{_probe_host(host)}:{port}/"
    if not _has_session(session_name):
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    if _probe_root(host, port):
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)


def wait_ready(host: str, port: int, timeout_s: float) -> bool:
    """Poll the HTTP root until it returns 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _probe_root(host, port):
            return True
        time.sleep(_DEFAULT_HEALTH_POLL_S)
    return False


def _has_session(name: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", name],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _probe_host(host: str) -> str:
    """Translate a bind address into one we can connect to.

    ``0.0.0.0`` and ``::`` are bind-only addresses; clients must reach
    the service via a real loopback address instead.
    """
    if host in ("0.0.0.0", "::"):
        return "127.0.0.1"
    return host


def _probe_root(host: str, port: int) -> bool:
    """Single HTTP root probe. Returns True iff 200."""
    try:
        with urllib.request.urlopen(
            f"http://{_probe_host(host)}:{port}/", timeout=1
        ) as response:
            return response.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


__all__ = [
    "is_running",
    "start_cptr",
    "status",
    "stop_cptr",
    "wait_ready",
]