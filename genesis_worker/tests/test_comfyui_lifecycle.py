"""Tests for ``lifecycle`` module — start/stop/status/wait_ready dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from genesis_worker.contracts import ServiceState, StartResult, StopResult
from genesis_worker.services.comfyui import lifecycle


def _start_kwargs(tmp_path: Path, **overrides):
    """Default kwargs for ``start_comfyui``. ``vault_models_dir`` defaults to a tmp path."""
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
        "vault_models_dir": tmp_path / "vault" / "comfyui",
        "media_input_dir": tmp_path / "media-vault" / "comfyui" / "inputs",
        "media_output_dir": tmp_path / "media-vault" / "comfyui" / "outputs",
    }
    base.update(overrides)
    return base


def test_start_returns_failure_when_image_not_present(tmp_path: Path) -> None:
    result = lifecycle.start_comfyui(**_start_kwargs(tmp_path, image_present=False))
    assert result.ok is False
    assert "image not pulled" in result.message


def test_start_dispatches_to_docker_container(tmp_path: Path) -> None:
    """When image is present, lifecycle delegates to ``DockerContainer.run``."""
    sentinel = StartResult(ok=True, message="started comfyui")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        result = lifecycle.start_comfyui(**_start_kwargs(tmp_path))
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


def test_start_skips_gpu_args_when_runtime_none(tmp_path: Path) -> None:
    """CPU-only path: runtime is None, gpu_flags is None."""
    sentinel = StartResult(ok=True, message="started")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        lifecycle.start_comfyui(**_start_kwargs(tmp_path, runtime=None, gpu_flags=None))
    kwargs = mock_run.call_args.kwargs
    assert kwargs["runtime"] is None
    assert kwargs["gpu_flags"] is None


def test_start_calls_docker_run(tmp_path: Path) -> None:
    """The lifecycle calls ``DockerContainer.run``; that method itself calls ``remove``."""
    sentinel = StartResult(ok=True, message="started")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel) as mock_run:
        lifecycle.start_comfyui(**_start_kwargs(tmp_path))
    assert mock_run.called


def test_start_creates_vault_models_dir_when_missing(tmp_path: Path) -> None:
    """Before calling ``container.run``, the lifecycle ensures ``vault_models_dir`` exists.

    Without this, ``--models-directory /vault/comfyui`` would point at a
    directory that doesn't exist inside the container, and ComfyUI's CLI
    would refuse to start on a fresh install with no symlinks yet.
    """
    sentinel = StartResult(ok=True, message="started")
    target = tmp_path / "vault" / "comfyui"
    assert not target.exists()  # precondition
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel):
        lifecycle.start_comfyui(**_start_kwargs(tmp_path, vault_models_dir=target))
    assert target.is_dir()


def test_start_idempotent_when_vault_models_dir_already_exists(tmp_path: Path) -> None:
    """Re-starting with the directory already present doesn't raise."""
    sentinel = StartResult(ok=True, message="started")
    target = tmp_path / "vault" / "comfyui"
    target.mkdir(parents=True)
    (target / "existing-file").write_text("keep me")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel):
        lifecycle.start_comfyui(**_start_kwargs(tmp_path, vault_models_dir=target))
    assert (target / "existing-file").exists()


def test_start_creates_media_input_output_dirs_when_missing(tmp_path: Path) -> None:
    """ADR-037: the media input/output bind-mount targets are pre-created.

    Without pre-creation, docker would initialise them as root, leaving
    the host user unable to remove the contents.
    """
    sentinel = StartResult(ok=True, message="started")
    inp = tmp_path / "media-vault" / "comfyui" / "inputs"
    out = tmp_path / "media-vault" / "comfyui" / "outputs"
    assert not inp.exists()
    assert not out.exists()
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel):
        lifecycle.start_comfyui(
            **_start_kwargs(
                tmp_path,
                media_input_dir=inp,
                media_output_dir=out,
            )
        )
    assert inp.is_dir()
    assert out.is_dir()


def test_start_idempotent_when_media_dirs_already_exist(tmp_path: Path) -> None:
    """Re-starting with the media dirs already present doesn't raise."""
    sentinel = StartResult(ok=True, message="started")
    inp = tmp_path / "media-vault" / "comfyui" / "inputs"
    out = tmp_path / "media-vault" / "comfyui" / "outputs"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    (inp / "existing-input").write_text("keep me")
    (out / "existing-output").write_text("keep me")
    with patch.object(lifecycle.DockerContainer, "run", return_value=sentinel):
        lifecycle.start_comfyui(
            **_start_kwargs(
                tmp_path,
                media_input_dir=inp,
                media_output_dir=out,
            )
        )
    assert (inp / "existing-input").exists()
    assert (out / "existing-output").exists()


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
