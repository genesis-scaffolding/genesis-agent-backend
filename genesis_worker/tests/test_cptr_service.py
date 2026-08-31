"""Tests for the cptr service plugin (the InferenceService-shaped facade)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from genesis_worker.contracts import ServiceState
from genesis_worker.services.cptr.service import CptrService
from genesis_worker.tests._factories import service_ctx

# --- construction ----------------------------------------------------------


def test_construction_uses_default_options(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert svc.name == "cptr"
    assert svc.display_name == "Open WebUI Computer"
    assert svc.listen_address == "0.0.0.0:4321"


def test_construction_applies_options(tmp_path: Path) -> None:
    svc = CptrService(
        service_ctx(
            tmp_path,
            name="cptr",
            options={"listen_host": "127.0.0.1", "listen_port": 9000},
        )
    )
    assert svc.listen_address == "127.0.0.1:9000"


def test_construction_defaults_log_file_to_log_dir(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert svc._log_file == tmp_path / "log" / "cptr.log"  # noqa: SLF001


def test_construction_respects_log_file_option(tmp_path: Path) -> None:
    custom = tmp_path / "my.log"
    svc = CptrService(service_ctx(tmp_path, name="cptr", options={"log_file": str(custom)}))
    assert svc._log_file == custom  # noqa: SLF001


# --- capabilities ----------------------------------------------------------


def test_capabilities_match_contract(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    caps = svc.capabilities()
    assert caps.has_web_ui is True
    assert caps.can_install is True
    assert caps.can_generate_config is False
    assert caps.can_export_for_agent is False
    assert caps.can_serve_llm is False
    assert caps.can_serve_image is False
    assert caps.can_train_models is False


# --- availability / installs -----------------------------------------------


def test_is_available_true_when_binary_on_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    assert CptrService(service_ctx(tmp_path, name="cptr")).is_available() is True


def test_is_available_false_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert CptrService(service_ctx(tmp_path, name="cptr")).is_available() is False


def test_installs_returns_cptr_installable(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    installs = svc.installs()
    assert len(installs) == 1
    assert installs[0].name == "cptr"
    assert svc.primary_installable() is installs[0]


# --- endpoints -------------------------------------------------------------


def test_web_ui_endpoint_none_when_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    monkeypatch.setattr(svc, "is_running", lambda: False)
    assert svc.web_ui_endpoint() is None


def test_web_ui_endpoint_uses_public_host_and_listen_port(tmp_path: Path, monkeypatch) -> None:
    svc = CptrService(
        service_ctx(
            tmp_path,
            name="cptr",
            options={"public_host": "my-host", "listen_port": 7777},
        )
    )
    monkeypatch.setattr(svc, "is_running", lambda: True)
    assert svc.web_ui_endpoint() == "http://my-host:7777/"


def test_web_ui_endpoint_falls_back_to_socket_gethostname(tmp_path: Path, monkeypatch) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    monkeypatch.setattr(svc, "is_running", lambda: True)

    class FakeSocket:
        def gethostname(self) -> str:
            return "live-host"

    monkeypatch.setattr("socket.gethostname", lambda: "live-host")
    assert svc.web_ui_endpoint() == "http://live-host:4321/"


def test_runtime_endpoint_is_none(tmp_path: Path) -> None:
    """cptr is web-UI only — no OpenAI-compatible API."""
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert svc.runtime_endpoint() is None


# --- start / stop dispatch -------------------------------------------------


def test_start_refuses_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    r = svc.start()
    assert r.ok is False
    assert "not installed" in r.message


def test_start_dispatches_to_lifecycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with patch(
        "genesis_worker.services.cptr.service.lifecycle.start_cptr",
        return_value=type("R", (), {"ok": True, "message": "ok"})(),
    ) as mock_start:
        svc.start()
    assert mock_start.called
    kwargs = mock_start.call_args.kwargs
    assert kwargs["port"] == 4321
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["session_name"] == "cptr"


def test_stop_dispatches_to_lifecycle(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with patch(
        "genesis_worker.services.cptr.service.lifecycle.stop_cptr",
        return_value=type("R", (), {"ok": True, "message": "ok"})(),
    ) as mock_stop:
        svc.stop()
    assert mock_stop.called


# --- uninstall guard ------------------------------------------------------


def test_uninstall_installable_refuses_while_running(tmp_path: Path, monkeypatch) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    monkeypatch.setattr(svc, "is_running", lambda: True)
    with pytest.raises(RuntimeError, match="cannot uninstall"):
        svc.uninstall_installable("cptr")


def test_uninstall_installable_unknown_name_raises_keyerror(tmp_path: Path, monkeypatch) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with pytest.raises(KeyError, match="unknown installable"):
        svc.uninstall_installable("does-not-exist")


def test_uninstall_installable_delegates_when_stopped(tmp_path: Path, monkeypatch) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with patch.object(svc.installs()[0], "uninstall") as mock_uninst:
        svc.uninstall_installable("cptr")
    assert mock_uninst.called


# --- status / wait_ready dispatch -----------------------------------------


def test_status_dispatches_to_lifecycle(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    sentinel = type("S", (), {"state": ServiceState.RUNNING, "endpoint": "x"})()
    with patch(
        "genesis_worker.services.cptr.service.lifecycle.status",
        return_value=sentinel,
    ) as mock_status:
        out = svc.status()
    assert out is sentinel
    assert mock_status.call_args.args == ("cptr", "0.0.0.0", 4321)


def test_wait_ready_dispatches_to_lifecycle(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with patch(
        "genesis_worker.services.cptr.service.lifecycle.wait_ready",
        return_value=True,
    ) as mock_wait:
        assert svc.wait_ready(5.0) is True
    assert mock_wait.call_args.args == ("0.0.0.0", 4321, 5.0)


# --- tail_log --------------------------------------------------------------


def test_tail_log_empty_when_log_missing(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert svc.tail_log() == ""


def test_tail_log_returns_last_n_bytes(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    svc._log_file.parent.mkdir(parents=True, exist_ok=True)  # noqa: SLF001
    svc._log_file.write_text("hello\nworld\nlast\n")  # noqa: SLF001
    assert "last" in svc.tail_log()


def test_tail_log_handles_short_file(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    svc._log_file.parent.mkdir(parents=True, exist_ok=True)  # noqa: SLF001
    svc._log_file.write_text("hi")  # noqa: SLF001
    assert svc.tail_log(n_bytes=8192) == "hi"


# --- ui_pages --------------------------------------------------------------


def test_ui_pages_has_only_status(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    pages = svc.ui_pages
    assert len(pages) == 1
    assert pages[0].label == "Status"
    # url_path must be set explicitly so it doesn't collide with the
    # llama_swap Status page (both have status.py; Streamlit would
    # infer /status for both and refuse to start).
    assert pages[0].url_path == "cptr_status"


# --- installed_version -----------------------------------------------------


def test_installed_version_proxies_to_installable(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with patch.object(svc.installs()[0], "installed_version", return_value="1.2.3"):
        assert svc.installed_version == "1.2.3"


def test_installed_version_none_when_not_installed(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with patch.object(svc.installs()[0], "installed_version", return_value=None):
        assert svc.installed_version is None


# --- resource estimate -----------------------------------------------------


def test_resource_estimate_modest(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    est = svc.resource_estimate()
    assert est.cpu_cores_recommended == 2
    assert est.vram_bytes_min == 0
    assert est.vram_bytes_typical == 0
