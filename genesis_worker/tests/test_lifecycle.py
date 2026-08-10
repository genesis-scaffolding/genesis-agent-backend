"""Tests for the tmux + curl lifecycle used by :class:`LlamaSwapService`.

These tests do NOT use the real ``llama-swap`` binary. They build a
small fake shim under ``tmp_path`` that wraps ``python3 -m http.server``
to serve a static ``/v1/models`` response on a free port. The shim is
prepended to ``PATH`` so ``shutil.which(\"llama-swap\")`` finds it.

The shim is intentionally tiny: a bash script that execs the
http.server with a JSON file. The shim ignores most of ``llama-swap``'s
CLI flags (``--config``, ``-listen``, ``-watch-config``) because the
tests only care that the binary runs and serves a 200 response on
``/v1/models``. ``wait_ready`` polls that endpoint; once it succeeds,
``status`` reports ``RUNNING``.

Tests that need a free port pick one by binding a socket, getting the
port number, then closing the socket — racing the kernel on the gap is
fine for the test-only loopback port range and far simpler than
scanning.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from genesis_worker.contracts import ServiceState
from genesis_worker.services.llama_swap import lifecycle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind a socket, get its port, close. Returns the port number.

    Race-prone in theory (the port could be claimed between close and
    use) but reliable enough on the loopback range for test fixtures.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_models_json(tmp_path: Path) -> Path:
    """Write a minimal /v1/models response to disk; returns its path."""
    body = {"object": "list", "data": [{"id": "fake-model", "object": "model"}]}
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(body))
    return models_file


def _make_fake_llama_swap(tmp_path: Path, models_file: Path, port: int) -> Path:
    """Build a bash shim that serves ``models_file`` on the given port.

    Returns the path to the shim (caller is responsible for ``chmod +x``).
    """
    shim = tmp_path / "llama-swap"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "# Fake llama-swap shim. Ignores flags; serves a static /v1/models.\n"
        f"exec python3 -m http.server {port} --directory {tmp_path}\n"
    )
    shim.chmod(0o755)
    return shim


class _StaticHandler(http.server.BaseHTTPRequestHandler):
    """http.server handler that returns a static JSON body for /v1/models."""

    body: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Silence the test output; the shim is noise.
        return


def _serve_in_background(port: int, body: bytes) -> threading.Thread:
    """Start an http.server serving ``body`` on /v1/models. Returns the thread."""
    _StaticHandler.body = body

    def _serve() -> None:
        server = http.server.HTTPServer(("127.0.0.1", port), _StaticHandler)
        try:
            server.serve_forever()
        finally:
            server.server_close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Status probe (no tmux required)
# ---------------------------------------------------------------------------


def test_status_returns_stopped_when_no_session() -> None:
    """With no tmux session, status reports STOPPED with the endpoint set."""
    s = lifecycle.status("definitely-not-running-12345", "127.0.0.1:1")
    assert s.state == ServiceState.STOPPED
    assert s.endpoint == "http://127.0.0.1:1/v1"


def test_wait_ready_returns_true_when_endpoint_responds(tmp_path: Path) -> None:
    """wait_ready succeeds against a background http.server."""
    port = _free_port()
    body = json.dumps({"data": []}).encode()
    _serve_in_background(port, body)
    assert lifecycle.wait_ready(f"127.0.0.1:{port}", timeout_s=2.0) is True


def test_wait_ready_returns_false_on_timeout() -> None:
    """wait_ready fails when nothing is listening."""
    port = _free_port()  # unused
    assert lifecycle.wait_ready(f"127.0.0.1:{port}", timeout_s=0.5) is False


# ---------------------------------------------------------------------------
# End-to-end start/stop via the fake llama-swap shim
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_swap_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a fake llama-swap on PATH and a writable /v1/models response.

    Yields the (config_path, listen_addr, session_name, log_file) tuple
    the lifecycle helpers expect. Cleans up the tmux session on teardown.
    """
    port = _free_port()
    models_file = _make_models_json(tmp_path)
    _make_fake_llama_swap(tmp_path, models_file, port)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ.get('PATH', '')}")

    config = tmp_path / "config.yaml"
    config.write_text("# fake config for test\n")
    listen = f"127.0.0.1:{port}"
    session = "swap-test"
    log = tmp_path / "swap.log"

    yield config, listen, session, log

    # Teardown: kill the session if it's still around.
    import subprocess

    subprocess.run(["tmux", "kill-session", "-t", session], check=False, capture_output=True)


def test_start_then_status_running_then_stop(fake_swap_env) -> None:
    """Full lifecycle: start -> status==RUNNING -> stop -> status==STOPPED."""
    config, listen, session, log = fake_swap_env

    # Start serves /v1/models from the http.server we already started.
    body = json.dumps({"data": [{"id": "x"}]}).encode()
    port = int(listen.split(":")[1])
    _serve_in_background(port, body)

    result = lifecycle.start_swap(
        config=config, listen_addr=listen, session_name=session,
        log_file=log, health_timeout_s=5.0,
    )
    assert result.ok, f"start_swap failed: {result.message}"
    assert result.message == f"started {session}"

    assert lifecycle.is_running(session) is True
    s = lifecycle.status(session, listen)
    assert s.state == ServiceState.RUNNING
    assert s.endpoint == f"http://{listen}/v1"

    stop = lifecycle.stop_swap(session)
    assert stop.ok
    assert lifecycle.is_running(session) is False
    assert lifecycle.status(session, listen).state == ServiceState.STOPPED


def test_stop_is_idempotent(fake_swap_env) -> None:
    """stop_swap returns ok=True when there's nothing to stop."""
    _, _, session, _ = fake_swap_env
    result = lifecycle.stop_swap(session)
    assert result.ok
    assert "no session" in result.message


def test_start_fails_when_binary_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no llama-swap on PATH, start_swap returns ok=False with a clear message."""
    # Strip PATH down to just essentials (no llama-swap).
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    config = tmp_path / "config.yaml"
    config.write_text("x: 1\n")
    result = lifecycle.start_swap(
        config=config, listen_addr="127.0.0.1:1",
        session_name="swap-noop", log_file=tmp_path / "log",
        health_timeout_s=0.1,
    )
    assert result.ok is False
    assert "PATH" in result.message


def test_start_fails_when_config_missing(tmp_path: Path) -> None:
    """A missing config file fails before any tmux activity."""
    result = lifecycle.start_swap(
        config=tmp_path / "missing.yaml",
        listen_addr="127.0.0.1:1",
        session_name="swap-noop",
        log_file=tmp_path / "log",
        health_timeout_s=0.1,
    )
    assert result.ok is False
    assert "config not found" in result.message


def test_is_running_false_when_session_absent() -> None:
    assert lifecycle.is_running("never-created-session") is False