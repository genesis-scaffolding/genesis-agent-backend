"""Tests for the ComfyUI service plugin (the InferenceService-shaped facade)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from genesis_worker.contracts import (
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
)
from genesis_worker.services.comfyui.service import ComfyUiService
from genesis_worker.services.comfyui.symlinks import SymlinkApplier
from genesis_worker.tests._factories import service_ctx

# --- construction ----------------------------------------------------------


def test_construction_uses_default_options(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc.name == "comfyui"
    assert svc.display_name == "ComfyUI"
    assert svc.listen_address == "0.0.0.0:8188"


def test_construction_applies_options(tmp_path: Path) -> None:
    svc = ComfyUiService(
        service_ctx(
            tmp_path,
            name="comfyui",
            options={"listen_port": 9999, "image_tag": "v0.99.0-cuda-13.0-amd64"},
        )
    )
    assert svc.listen_address == "0.0.0.0:9999"
    assert svc.image_ref == "ghcr.io/genesis-scaffolding/comfyui-cuda:v0.99.0-cuda-13.0-amd64"


def test_construction_defaults_log_file_to_log_dir(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc.log_file == tmp_path / "log" / "comfyui.log"


def test_construction_respects_log_file_option(tmp_path: Path) -> None:
    custom = tmp_path / "my.log"
    svc = ComfyUiService(
        service_ctx(tmp_path, name="comfyui", options={"log_file": str(custom)})
    )
    assert svc.log_file == custom


def test_construction_defaults_vault_models_dir(tmp_path: Path) -> None:
    """Bind mount root for ComfyUI models comes from ``ctx.vault_path`` by default."""
    vault = tmp_path / "myvault"
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui", vault_path=vault))
    assert svc._vault_models_dir == vault / "comfyui"


def test_construction_data_dirs_default_under_data_dir(tmp_path: Path) -> None:
    # ctx.data_dir is already scoped to the service by the framework;
    # the service appends its subdirectories directly.
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc._data_python_dir == tmp_path / "data" / "data" / "python"
    assert svc._data_custom_nodes_dir == tmp_path / "data" / "data" / "custom_nodes"
    assert svc._data_input_dir == tmp_path / "data" / "data" / "input"
    assert svc._data_output_dir == tmp_path / "data" / "data" / "output"
    assert svc._data_profiles_dir == tmp_path / "data" / "data" / "profiles"


def test_construction_symlinks_file_default(tmp_path: Path) -> None:
    # ctx.config_dir is already scoped to the service by the framework.
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc._symlinks_file == tmp_path / "config" / "model_symlink.yaml"


def test_construction_puid_pgid_auto_default(tmp_path: Path) -> None:
    """PUID/PGID default to ``os.getuid``/``os.getgid`` when not supplied."""
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc._puid == os.getuid()
    assert svc._pgid == os.getgid()


def test_construction_puid_pgid_overrides(tmp_path: Path) -> None:
    svc = ComfyUiService(
        service_ctx(tmp_path, name="comfyui", options={"puid": 1234, "pgid": 5678})
    )
    assert svc._puid == 1234
    assert svc._pgid == 5678


def test_construction_has_nvidia_gpu_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The GPU probe runs once at construction and is cached."""
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.service._has_nvidia_gpu",
        lambda: True,
    )
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc.has_nvidia_gpu is True


# --- capabilities ----------------------------------------------------------


def test_capabilities_match_contract(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    caps = svc.capabilities()
    assert caps.has_web_ui is True
    assert caps.can_install is True
    assert caps.can_serve_image is True
    assert caps.can_generate_config is False
    assert caps.can_export_for_agent is False
    assert caps.can_serve_llm is False
    assert caps.can_train_models is False


# --- availability / installs -----------------------------------------------


def test_is_available_false_when_image_not_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.install.DockerContainer.image_present",
        staticmethod(lambda image: False),
    )
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc.is_available() is False


def test_is_available_true_when_image_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.install.DockerContainer.image_present",
        staticmethod(lambda image: True),
    )
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc.is_available() is True


def test_is_available_does_not_consult_binary_path(tmp_path: Path) -> None:
    """Override of the cptr/llama-swap convention.

    Container services invert the relationship: ``binary_path()`` returns
    ``None`` because there's no host binary to invoke. Availability
    is decided by ``state() == INSTALLED`` instead.
    """
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc._install.binary_path() is None
    # ``is_available`` works regardless of ``binary_path`` being None.


def test_installs_returns_comfyui_image(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    installs = svc.installs()
    assert len(installs) == 1
    assert installs[0].name == "comfyui-cuda"
    assert svc.primary_installable() is installs[0]


# --- endpoints -------------------------------------------------------------


def test_runtime_endpoint_is_none(tmp_path: Path) -> None:
    """ComfyUI has no OpenAI-compatible API."""
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert svc.runtime_endpoint() is None


def test_web_ui_endpoint_none_when_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    monkeypatch.setattr(svc, "is_running", lambda: False)
    assert svc.web_ui_endpoint() is None


def test_web_ui_endpoint_uses_public_host_and_listen_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = ComfyUiService(
        service_ctx(
            tmp_path,
            name="comfyui",
            options={"public_host": "my-host", "listen_port": 7777},
        )
    )
    monkeypatch.setattr(svc, "is_running", lambda: True)
    assert svc.web_ui_endpoint() == "http://my-host:7777/"


def test_web_ui_endpoint_falls_back_to_socket_gethostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    monkeypatch.setattr(svc, "is_running", lambda: True)
    monkeypatch.setattr("socket.gethostname", lambda: "live-host")
    assert svc.web_ui_endpoint() == "http://live-host:8188/"


# --- start / stop / status / wait_ready ----------------------------------


def test_start_refuses_when_no_gpu_and_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.service._has_nvidia_gpu",
        lambda: False,
    )
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    r = svc.start()
    assert r.ok is False
    assert "no NVIDIA GPU" in r.message


def test_start_refuses_when_image_not_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.service._has_nvidia_gpu",
        lambda: True,
    )
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.install.DockerContainer.image_present",
        staticmethod(lambda image: False),
    )
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    r = svc.start()
    assert r.ok is False
    assert "image not pulled" in r.message


def test_start_dispatches_to_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.service._has_nvidia_gpu",
        lambda: True,
    )
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.install.DockerContainer.image_present",
        staticmethod(lambda image: True),
    )
    monkeypatch.setattr(
        "genesis_worker.utils.process.docker.DockerContainer.nvidia_runtime_available",
        staticmethod(lambda: True),
    )
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    sentinel = StartResult(ok=True, message="ok")
    with patch(
        "genesis_worker.services.comfyui.lifecycle.start_comfyui",
        return_value=sentinel,
    ) as mock_start:
        r = svc.start()
    assert r is sentinel
    kwargs = mock_start.call_args.kwargs
    assert kwargs["image"] == svc.image_ref
    assert kwargs["listen_port"] == 8188
    assert kwargs["container_name"] == "comfyui"
    assert kwargs["volumes"]["/vault"] == str(svc._vault_models_dir.parent)
    extra_paths_file = svc._vault_models_dir / "extra_model_paths.yaml"
    assert kwargs["extra_args"][:2] == ["--extra-model-paths", str(extra_paths_file)]


def test_start_skips_gpu_args_when_runtime_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.service._has_nvidia_gpu",
        lambda: True,
    )
    monkeypatch.setattr(
        "genesis_worker.services.comfyui.install.DockerContainer.image_present",
        staticmethod(lambda image: True),
    )
    monkeypatch.setattr(
        "genesis_worker.utils.process.docker.DockerContainer.nvidia_runtime_available",
        staticmethod(lambda: False),
    )
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    with patch("genesis_worker.services.comfyui.lifecycle.start_comfyui") as mock_start:
        svc.start()
    kwargs = mock_start.call_args.kwargs
    assert kwargs["runtime"] is None
    assert kwargs["gpu_flags"] is None


def test_stop_dispatches_to_lifecycle(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    sentinel = StopResult(ok=True, message="ok")
    with patch(
        "genesis_worker.services.comfyui.lifecycle.stop_comfyui",
        return_value=sentinel,
    ):
        r = svc.stop()
    assert r is sentinel


def test_status_dispatches_to_lifecycle(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    sentinel = ServiceStatus(state=ServiceState.RUNNING, endpoint="http://x/")
    with patch(
        "genesis_worker.services.comfyui.lifecycle.status_comfyui",
        return_value=sentinel,
    ) as mock_status:
        out = svc.status()
    assert out is sentinel
    assert mock_status.call_args.args == ("comfyui", "0.0.0.0", 8188)


def test_wait_ready_dispatches_to_lifecycle(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    with patch(
        "genesis_worker.services.comfyui.lifecycle.wait_ready_comfyui",
        return_value=True,
    ) as mock_wait:
        assert svc.wait_ready(15.0) is True
    assert mock_wait.call_args.args == ("0.0.0.0", 8188, 15.0)


# --- uninstall guard ------------------------------------------------------


def test_uninstall_installable_refuses_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    monkeypatch.setattr(svc, "is_running", lambda: True)
    with pytest.raises(RuntimeError, match="cannot uninstall"):
        svc.uninstall_installable("comfyui-cuda")


def test_uninstall_installable_unknown_name_raises_keyerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with pytest.raises(KeyError, match="unknown installable"):
        svc.uninstall_installable("does-not-exist")


def test_uninstall_installable_delegates_when_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with patch.object(svc.installs()[0], "uninstall") as mock_uninst:
        svc.uninstall_installable("comfyui-cuda")
    assert mock_uninst.called


# --- tail_log --------------------------------------------------------------


def test_tail_log_returns_docker_logs_output(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    with patch(
        "genesis_worker.services.comfyui.lifecycle.logs_comfyui",
        return_value="last line\n",
    ) as mock_logs:
        out = svc.tail_log()
    assert out == "last line\n"
    # The byte count is converted to an approximate line count.
    assert mock_logs.call_args.args[0] == "comfyui"
    assert mock_logs.call_args.args[1] >= 50  # minimum 50 lines


# --- ui_pages --------------------------------------------------------------


def test_ui_pages_has_three_pages_with_explicit_url_paths(tmp_path: Path) -> None:
    """Three pages; explicit url_path to avoid slug collisions."""
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    pages = svc.ui_pages
    assert [p.label for p in pages] == ["Status", "Image", "Models"]
    assert [p.url_path for p in pages] == ["comfyui_status", "comfyui_image", "comfyui_models"]


# --- resource estimate ----------------------------------------------------


def test_resource_estimate(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    est = svc.resource_estimate()
    assert est.cpu_cores_recommended == 4
    assert est.vram_bytes_typical == 12_000_000_000
    assert est.vram_bytes_min == 6_000_000_000


# --- symlink applier property ---------------------------------------------


def test_symlinks_property_returns_applier(tmp_path: Path) -> None:
    svc = ComfyUiService(service_ctx(tmp_path, name="comfyui"))
    assert isinstance(svc.symlinks, SymlinkApplier)
    assert svc.symlinks._vault_models_dir == svc._vault_models_dir
