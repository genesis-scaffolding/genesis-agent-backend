"""tmux + curl lifecycle for llama-swap."""

from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path

from ...contracts import ServiceState, ServiceStatus, StartResult, StopResult
from ...utils.net import HealthProbe
from ...utils.process import TmuxProcess

_DEFAULT_HEALTH_POLL_S = 1.0
_DEFAULT_HEALTH_TIMEOUT_S = 60.0


def _no_llama_servers() -> bool:
    """True iff no ``llama-server`` processes are running on the system."""
    result = subprocess.run(
        ["pgrep", "-f", "llama-server"],
        check=False,
        capture_output=True,
    )
    return result.returncode != 0


def _wait_for_children_gone(timeout_s: float) -> bool:
    """Poll for ``llama-server`` processes; return True if none within the timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _no_llama_servers():
            return True
        time.sleep(0.5)
    return _no_llama_servers()


class _LlamaSwapTmuxProcess(TmuxProcess):
    """TmuxProcess with llama-server child-drain baked in."""

    def _kill_children(self) -> None:
        subprocess.run(
            ["pkill", "-9", "-f", "llama-server"],
            check=False,
            capture_output=True,
        )


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

    tmux = _LlamaSwapTmuxProcess(session_name)

    # Drop any stray llama-server so llama-swap can own the port.
    subprocess.run(
        ["pkill", "-9", "-f", "llama-server"],
        check=False,
        capture_output=True,
    )
    time.sleep(1)

    cmd = (
        f"{shlex.quote(str(binary))} --config {shlex.quote(str(config))} "
        f"-listen {listen_addr} -watch-config"
    )
    result = tmux.start(cmd, log_file)
    if not result.ok:
        return result

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

    Falls back to a hard child cleanup if the graceful shutdown stalls
    past ``shutdown_timeout_s``.
    """
    tmux = _LlamaSwapTmuxProcess(session_name)
    # If start() stored child-drain params on the instance, keep them;
    # otherwise use the value passed here (for direct stop_swap calls in tests).
    if not hasattr(tmux, "_wait_for_children"):
        tmux._wait_for_children = _no_llama_servers
    if not hasattr(tmux, "_child_wait_timeout_s"):
        tmux._child_wait_timeout_s = shutdown_timeout_s
    return tmux.stop()


def _probe_models(host: str, port: int) -> bool:
    """Single /v1/models probe. Returns True iff 200. Exposed for tests."""
    return HealthProbe(host, port, probe_path="/v1/models").probe()


def _has_session(name: str) -> bool:
    """True iff a tmux session named ``name`` exists. Exposed for tests."""
    return TmuxProcess.has_session(name)


def is_running(session_name: str) -> bool:
    """True iff the named tmux session exists."""
    return TmuxProcess(session_name).exists()


def status(session_name: str, listen_addr: str) -> ServiceStatus:
    """Coarse status: session presence + a /v1/models probe.

    - session absent → ``STOPPED``
    - session present, /v1/models returns 200 → ``RUNNING`` with endpoint
    - session present, probe fails or returns non-200 → ``STARTING``
    """
    host, port_str = listen_addr.rsplit(":", 1)
    endpoint = f"http://{listen_addr}/v1"
    if not _has_session(session_name):
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    if _probe_models(host, int(port_str)):
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)


def wait_ready(listen_addr: str, timeout_s: float) -> bool:
    """Poll ``/v1/models`` until it returns 200 or the timeout elapses."""
    host, port_str = listen_addr.rsplit(":", 1)
    return HealthProbe(host, int(port_str), probe_path="/v1/models").wait_ready(
        timeout_s
    )


__all__ = [
    "is_running",
    "start_swap",
    "status",
    "stop_swap",
    "wait_ready",
]
