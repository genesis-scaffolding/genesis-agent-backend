"""tmux + curl lifecycle for llama-swap."""

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


def start_swap(
    binary: Path,
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
    if not binary.is_file():
        return StartResult(ok=False, message=f"binary not found: {binary}")
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
        f"{shlex.quote(str(binary))} --config {shlex.quote(str(config))} "
        f"-listen {listen_addr} -watch-config 2>&1 | tee -a {shlex.quote(str(log_file))}"
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


def stop_swap(
    session_name: str,
    shutdown_timeout_s: float = 30.0,
) -> StopResult:
    """Stop the llama-swap tmux session, with graceful child cleanup.

    Sends Ctrl-C to the session's foreground process group (bash running
    the ``llama-swap ... | tee ...`` pipeline), waits for spawned
    ``llama-server`` children to release VRAM, then tears down tmux.

    Without this wait, killing the tmux session delivers SIGHUP to bash;
    bash exits, the pipe breaks, llama-swap dies from SIGPIPE without
    running its child-cleanup path, and any spawned ``llama-server``
    processes are reparented to init while still holding their VRAM.
    We saw this leak in practice: stopping llama-swap left a 14 GB
    ``llama-server`` orphan running.

    Falls back to a hard child cleanup if the graceful shutdown stalls
    past ``shutdown_timeout_s``. Tolerates the tmux session having
    already exited on its own (e.g. when ``remain-on-exit`` is off and
    Ctrl-C killed the pane process) — in that case the wait loop
    confirms no children remain and we report success.
    """
    if not _has_session(session_name):
        return StopResult(ok=True, message="no session")

    # 1. Deliver SIGINT to the foreground process group. tmux routes the
    # keypress to the pane's active process; bash propagates SIGINT to
    # llama-swap, which initiates graceful shutdown of its children.
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "C-c"],
        check=False,
        capture_output=True,
    )

    # 2. Wait for spawned llama-server children to exit. Each holds VRAM
    # until it terminates.
    graceful = _wait_for_children_gone(shutdown_timeout_s)

    # 3. Tear down the tmux session if it's still around. With the
    # default ``remain-on-exit off``, the session dies on its own when
    # the pane's foreground process exits — that's fine, treat it as
    # success.
    if _has_session(session_name):
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

    if not graceful:
        # Last resort: hard-kill any lingering children that ignored SIGTERM.
        subprocess.run(
            ["pkill", "-9", "-f", "llama-server"],
            check=False,
            capture_output=True,
        )
        return StopResult(
            ok=True,
            message=f"killed {session_name} (forced child cleanup after {shutdown_timeout_s:.0f}s timeout)",
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


def _wait_for_children_gone(timeout_s: float) -> bool:
    """Poll for ``llama-server`` processes; return True if none within the timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _no_llama_servers():
            return True
        time.sleep(0.5)
    return _no_llama_servers()


def _no_llama_servers() -> bool:
    """True iff no ``llama-server`` processes are running on the system."""
    result = subprocess.run(
        ["pgrep", "-f", "llama-server"],
        check=False,
        capture_output=True,
    )
    # pgrep returns 1 when no processes match — the case we want.
    return result.returncode != 0


def _probe_models(listen_addr: str) -> bool:
    """Single /v1/models probe. Returns True iff 200."""
    try:
        with urllib.request.urlopen(f"http://{listen_addr}/v1/models", timeout=1) as response:
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

