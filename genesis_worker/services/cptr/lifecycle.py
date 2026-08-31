"""tmux lifecycle for cptr."""

from __future__ import annotations

import shlex
import time
from pathlib import Path

from ...contracts import ServiceStatus, StartResult, StopResult
from ...utils.net import HealthProbe
from ...utils.process import TmuxProcess

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

    tmux = TmuxProcess(session_name)

    cmd = f"{shlex.quote(str(binary))} run --host {shlex.quote(host)} --port {port}"
    result = tmux.start(cmd, log_file)
    if not result.ok:
        return result

    if wait_ready(host, port, health_timeout_s):
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
    tmux = TmuxProcess(session_name)
    if not tmux.exists():
        return StopResult(ok=True, message="no session")

    tmux.send_interrupt()
    deadline = time.monotonic() + _GRACEFUL_STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not tmux.exists():
            return StopResult(ok=True, message=f"killed {session_name}")
        time.sleep(0.5)

    if tmux.exists():
        tmux.kill()
    return StopResult(ok=True, message=f"killed {session_name} (forced)")


def _probe_host(host: str) -> str:
    """Translate a bind address into a connectable address. Exposed for tests."""
    return HealthProbe.resolve_connect_host(host)


def _probe_root(host: str, port: int) -> bool:
    """Single HTTP root probe. Exposed for tests."""
    return HealthProbe(host, port, probe_path="/").probe()


def is_running(session_name: str) -> bool:
    """True iff the named tmux session exists."""
    return TmuxProcess(session_name).exists()


def status(session_name: str, host: str, port: int) -> ServiceStatus:
    """Coarse status: session presence + an HTTP root probe."""
    from ...contracts import ServiceState

    endpoint = f"http://{_probe_host(host)}:{port}/"
    if not TmuxProcess(session_name).exists():
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


__all__ = [
    "_probe_host",
    "_probe_root",
    "is_running",
    "start_cptr",
    "status",
    "stop_cptr",
    "wait_ready",
]
