"""Tests for ``lifecycle`` module — start/stop/status/wait_ready dispatch."""

from __future__ import annotations

from unittest.mock import patch

from genesis_worker.contracts import ServiceState, StartResult, StopResult
from genesis_worker.services.crawl4ai import lifecycle


def _start_kwargs(**overrides):
    """Default kwargs for ``start_crawl4ai``."""
    base = {
        "image": "unclecode/crawl4ai:latest",
        "image_present": True,
        "container_name": "crawl4ai",
        "listen_host": "0.0.0.0",
        "listen_port": 11235,
        "volumes": {"/app/data": "/host/data"},
        "env": {"PUID": "1000", "PGID": "1000"},
        "restart_policy": "unless-stopped",
        "hostname": "crawl4ai",
        "shm_size": "1g",
        "extra_args": [],
    }
    base.update(overrides)
    return base


def test_start_returns_failure_when_image_not_present() -> None:
    result = lifecycle.start_crawl4ai(**_start_kwargs(image_present=False))
    assert result.ok is False
    assert "image not pulled" in result.message


def test_start_dispatches_to_docker_container() -> None:
    """When image is present, lifecycle delegates to ``DockerContainer.run``."""
    sentinel = StartResult(ok=True, message="started crawl4ai")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        result = lifecycle.start_crawl4ai(**_start_kwargs())
    assert result is sentinel
    assert mock_run.called
    kwargs = mock_run.call_args.kwargs
    assert kwargs["image"] == "unclecode/crawl4ai:latest"
    # Internal port is fixed at 11235; host port mapping matches listen_port.
    assert kwargs["ports"] == {"11235/tcp": ("0.0.0.0", 11235)}
    assert kwargs["volumes"] == {"/app/data": "/host/data"}
    assert kwargs["env"] == {"PUID": "1000", "PGID": "1000"}
    assert kwargs["command"] == []
    assert kwargs["restart"] == "unless-stopped"
    assert kwargs["hostname"] == "crawl4ai"
    assert kwargs["shm_size"] == "1g"


def test_start_passes_extra_args_as_command() -> None:
    sentinel = StartResult(ok=True, message="started")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        lifecycle.start_crawl4ai(**_start_kwargs(extra_args=["--foo", "bar"]))
    assert mock_run.call_args.kwargs["command"] == ["--foo", "bar"]


def test_start_extra_args_none_passes_none() -> None:
    sentinel = StartResult(ok=True, message="started")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        lifecycle.start_crawl4ai(**_start_kwargs(extra_args=None))
    assert mock_run.call_args.kwargs["command"] is None


def test_start_passes_shm_size_none_through() -> None:
    """Lifecycle carries shm_size=None straight through when not needed."""
    sentinel = StartResult(ok=True, message="started")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        lifecycle.start_crawl4ai(**_start_kwargs(shm_size=None))
    assert mock_run.call_args.kwargs["shm_size"] is None


# --- stop -----------------------------------------------------------------


def test_stop_calls_docker_stop_then_remove() -> None:
    """``stop_crawl4ai`` invokes ``DockerContainer.stop`` then ``remove``."""
    sentinel_stop = StopResult(ok=True, message="stopped")
    with (
        patch.object(lifecycle.DockerContainer, "stop", return_value=sentinel_stop) as mock_stop,
        patch.object(lifecycle.DockerContainer, "remove") as mock_remove,
    ):
        result = lifecycle.stop_crawl4ai("crawl4ai")
    assert result is sentinel_stop
    assert mock_stop.called
    assert mock_remove.called


# --- is_running -----------------------------------------------------------


def test_is_running_delegates_to_docker_container() -> None:
    with patch.object(lifecycle.DockerContainer, "is_running", return_value=True) as mock_ir:
        assert lifecycle.is_running_crawl4ai("crawl4ai") is True
    assert mock_ir.called


# --- status ---------------------------------------------------------------


def test_status_returns_stopped_when_container_absent() -> None:
    with patch.object(lifecycle.DockerContainer, "is_running", return_value=False):
        status = lifecycle.status_crawl4ai("crawl4ai", "0.0.0.0", 11235)
    assert status.state == ServiceState.STOPPED
    assert status.endpoint == "http://127.0.0.1:11235/"


def test_status_returns_running_when_probe_succeeds() -> None:
    with (
        patch.object(lifecycle.DockerContainer, "is_running", return_value=True),
        patch.object(lifecycle.HealthProbe, "probe", return_value=True),
    ):
        status = lifecycle.status_crawl4ai("crawl4ai", "0.0.0.0", 11235)
    assert status.state == ServiceState.RUNNING


def test_status_returns_starting_when_running_but_probe_fails() -> None:
    with (
        patch.object(lifecycle.DockerContainer, "is_running", return_value=True),
        patch.object(lifecycle.HealthProbe, "probe", return_value=False),
    ):
        status = lifecycle.status_crawl4ai("crawl4ai", "0.0.0.0", 11235)
    assert status.state == ServiceState.STARTING


def test_default_probe_path_is_health() -> None:
    """The probe hits /health, not /, because / requires API-token auth.

    Constructs the probe the same way status_crawl4ai does to confirm
    the URL resolves to /health (which returns 200 without auth).
    """
    from genesis_worker.utils.net import HealthProbe

    probe = HealthProbe("0.0.0.0", 11235, probe_path=lifecycle._DEFAULT_HEALTH_PROBE_PATH)
    assert probe._url() == "http://127.0.0.1:11235/health"


# --- wait_ready -----------------------------------------------------------


def test_wait_ready_delegates_to_health_probe() -> None:
    with patch.object(lifecycle.HealthProbe, "wait_ready", return_value=True) as mock_wr:
        assert lifecycle.wait_ready_crawl4ai("0.0.0.0", 11235, 30.0) is True
    assert mock_wr.call_args.args == (30.0,)


# --- logs -----------------------------------------------------------------


def test_logs_delegates_to_docker_container() -> None:
    with patch.object(lifecycle.DockerContainer, "logs", return_value="tail line\n") as mock_logs:
        out = lifecycle.logs_crawl4ai("crawl4ai", 100)
    assert out == "tail line\n"
    assert mock_logs.call_args.kwargs == {"tail_lines": 100}
