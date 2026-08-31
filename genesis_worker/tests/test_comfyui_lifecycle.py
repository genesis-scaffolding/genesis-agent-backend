"""Tests for ``lifecycle`` module — start/stop/status/wait_ready dispatch."""

from __future__ import annotations

from unittest.mock import patch

from genesis_worker.contracts import ServiceState, StartResult, StopResult
from genesis_worker.services.comfyui import lifecycle


def _start_kwargs(**overrides):
    """Default kwargs for ``start_comfyui``."""
    base = {
        "image": "ghcr.io/genesis-scaffolding/comfyui-cuda:v1",
        "image_present": True,
        "container_name": "comfyui",
        "listen_host": "0.0.0.0",
        "listen_port": 8188,
        "volumes": {"/data/models": "/host/models"},
        "env": {"PUID": "1000", "PGID": "1000"},
        "runtime": "nvidia",
        "gpu_flags": ["driver=nvidia", "count=1"],
        "extra_args": ["--verbose"],
        "restart_policy": "unless-stopped",
        "hostname": "comfyui",
    }
    base.update(overrides)
    return base


def test_start_returns_failure_when_image_not_present() -> None:
    result = lifecycle.start_comfyui(**_start_kwargs(image_present=False))
    assert result.ok is False
    assert "image not pulled" in result.message


def test_start_dispatches_to_docker_container() -> None:
    """When image is present, lifecycle delegates to ``DockerContainer.run``."""
    sentinel = StartResult(ok=True, message="started comfyui")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        result = lifecycle.start_comfyui(**_start_kwargs())
    assert result is sentinel
    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    assert kwargs["image"] == "ghcr.io/genesis-scaffolding/comfyui-cuda:v1"
    assert kwargs["ports"] == {"8188/tcp": ("0.0.0.0", 8188)}
    assert kwargs["volumes"] == {"/data/models": "/host/models"}
    assert kwargs["env"] == {"PUID": "1000", "PGID": "1000"}
    assert kwargs["runtime"] == "nvidia"
    assert kwargs["gpu_flags"] == ["driver=nvidia", "count=1"]
    assert kwargs["command"] == ["--verbose"]
    assert kwargs["restart"] == "unless-stopped"
    assert kwargs["hostname"] == "comfyui"


def test_start_skips_gpu_args_when_runtime_none() -> None:
    """CPU-only path: runtime is None, gpu_flags is None."""
    sentinel = StartResult(ok=True, message="started")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        lifecycle.start_comfyui(**_start_kwargs(runtime=None, gpu_flags=None))
    kwargs = mock_run.call_args.kwargs
    assert kwargs["runtime"] is None
    assert kwargs["gpu_flags"] is None


def test_start_calls_docker_run() -> None:
    """The lifecycle calls ``DockerContainer.run``; that method itself calls ``remove``."""
    sentinel = StartResult(ok=True, message="started")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        lifecycle.start_comfyui(**_start_kwargs())
    assert mock_run.called


# --- stop -----------------------------------------------------------------


def test_stop_calls_docker_stop_then_remove() -> None:
    """``stop_comfyui`` invokes ``DockerContainer.stop`` then ``remove``."""
    sentinel_stop = StopResult(ok=True, message="stopped")
    with (
        patch.object(lifecycle.DockerContainer, "stop", return_value=sentinel_stop) as mock_stop,
        patch.object(lifecycle.DockerContainer, "remove") as mock_remove,
    ):
        result = lifecycle.stop_comfyui("comfyui")
    assert result is sentinel_stop
    assert mock_stop.called
    assert mock_remove.called


# --- is_running -----------------------------------------------------------


def test_is_running_delegates_to_docker_container() -> None:
    with patch.object(lifecycle.DockerContainer, "is_running", return_value=True) as mock_ir:
        assert lifecycle.is_running_comfyui("comfyui") is True
    # ``DockerContainer.is_running`` is invoked through the instance; the
    # descriptor binding is internal to Mock — we just verify the call happened.
    assert mock_ir.called


# --- status ---------------------------------------------------------------


def test_status_returns_stopped_when_container_absent() -> None:
    with patch.object(lifecycle.DockerContainer, "is_running", return_value=False):
        status = lifecycle.status_comfyui("comfyui", "0.0.0.0", 8188)
    assert status.state == ServiceState.STOPPED
    assert status.endpoint == "http://127.0.0.1:8188/"


def test_status_returns_running_when_probe_succeeds() -> None:
    with (
        patch.object(lifecycle.DockerContainer, "is_running", return_value=True),
        patch.object(lifecycle.HealthProbe, "probe", return_value=True),
    ):
        status = lifecycle.status_comfyui("comfyui", "0.0.0.0", 8188)
    assert status.state == ServiceState.RUNNING


def test_status_returns_starting_when_running_but_probe_fails() -> None:
    with (
        patch.object(lifecycle.DockerContainer, "is_running", return_value=True),
        patch.object(lifecycle.HealthProbe, "probe", return_value=False),
    ):
        status = lifecycle.status_comfyui("comfyui", "0.0.0.0", 8188)
    assert status.state == ServiceState.STARTING


# --- wait_ready -----------------------------------------------------------


def test_wait_ready_delegates_to_health_probe() -> None:
    with patch.object(lifecycle.HealthProbe, "wait_ready", return_value=True) as mock_wr:
        assert lifecycle.wait_ready_comfyui("0.0.0.0", 8188, 30.0) is True
    # ``wait_ready`` is called with timeout_s only; host/port are in the constructor.
    assert mock_wr.call_args.args == (30.0,)


# --- logs -----------------------------------------------------------------


def test_logs_delegates_to_docker_container() -> None:
    with patch.object(lifecycle.DockerContainer, "logs", return_value="tail line\n") as mock_logs:
        out = lifecycle.logs_comfyui("comfyui", 100)
    assert out == "tail line\n"
    assert mock_logs.call_args.kwargs == {"tail_lines": 100}
