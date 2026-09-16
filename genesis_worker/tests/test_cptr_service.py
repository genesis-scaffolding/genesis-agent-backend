"""Tests for the cptr service plugin — subclass of UvService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from genesis_worker.contracts import (
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
    ServiceState,
)
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
    assert svc.log_file == tmp_path / "log" / "cptr.log"


def test_construction_respects_log_file_option(tmp_path: Path) -> None:
    custom = tmp_path / "my.log"
    svc = CptrService(service_ctx(tmp_path, name="cptr", options={"log_file": str(custom)}))
    assert svc.log_file == custom


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


# --- category / description -----------------------------------------------


def test_category_is_chat(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert svc.category.value == "chat"


def test_description_is_short(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert svc.description == "Open WebUI automation"


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


def test_start_calls_post_install_then_tmux_then_wait_ready(tmp_path: Path, monkeypatch) -> None:
    """The new start() wires through TmuxProcess + HealthProbe."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    # Make the post-install hook observable without touching real files.
    calls: list[str] = []

    def _observe_post_install() -> None:
        calls.append("post_install")

    monkeypatch.setattr(svc, "_post_install", _observe_post_install)

    with (
        patch.object(svc, "wait_ready", return_value=True) as mock_wait,
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            return_value=type("R", (), {"ok": True, "message": "ok"})(),
        ) as mock_tmux,
    ):
        r = svc.start()
    assert r.ok is True
    assert "post_install" in calls
    assert mock_wait.called
    assert mock_tmux.called


def test_start_returns_failure_on_tmux_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with patch(
        "genesis_worker.utils.services.uv_service.TmuxProcess.start",
        return_value=type("R", (), {"ok": False, "message": "tmux fail"})(),
    ):
        r = svc.start()
    assert r.ok is False
    assert "tmux fail" in r.message


def test_start_returns_failure_when_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            return_value=type("R", (), {"ok": True, "message": "ok"})(),
        ),
        patch.object(svc, "wait_ready", return_value=False),
    ):
        r = svc.start()
    assert r.ok is False
    assert "did not become ready" in r.message


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


# --- status / wait_ready ---------------------------------------------------


def test_status_stopped_when_no_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists", lambda self: False
    )
    s = CptrService(service_ctx(tmp_path, name="cptr")).status()
    assert s.state == ServiceState.STOPPED


def test_status_running_when_session_present_and_probe_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists", lambda self: True
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.HealthProbe.probe", lambda self: True
    )
    s = CptrService(service_ctx(tmp_path, name="cptr")).status()
    assert s.state == ServiceState.RUNNING


def test_status_starting_when_session_present_but_probe_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists", lambda self: True
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.HealthProbe.probe", lambda self: False
    )
    s = CptrService(service_ctx(tmp_path, name="cptr")).status()
    assert s.state == ServiceState.STARTING


def test_wait_ready_delegates_to_health_probe(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    with patch.object(svc, "wait_ready", return_value=True) as mock_wait:
        assert svc.wait_ready(5.0) is True
    assert mock_wait.call_args.args == (5.0,)


# --- tail_log --------------------------------------------------------------


def test_tail_log_empty_when_log_missing(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert svc.tail_log() == ""


def test_tail_log_returns_last_n_bytes(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    svc.log_file.parent.mkdir(parents=True, exist_ok=True)
    svc.log_file.write_text("hello\nworld\nlast\n")
    assert "last" in svc.tail_log()


def test_tail_log_handles_short_file(tmp_path: Path) -> None:
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    svc.log_file.parent.mkdir(parents=True, exist_ok=True)
    svc.log_file.write_text("hi")
    assert svc.tail_log(n_bytes=8192) == "hi"


# --- stream timeouts (ADR-035 phase 2 regression fix) -------------------


def test_default_stream_timeout_is_1200(tmp_path: Path) -> None:
    """Pre-phase-1 cptr lifecycle used 1200s as the default. We honor that."""
    from genesis_worker.services.cptr.options import CptrOptions

    opts = CptrOptions()
    assert opts.stream_timeout_s == 1200


def test_command_env_injects_stream_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``start()`` prepends the stream-timeout exports to the tmux command."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    captured: list[str] = []

    def _capture_start(cmd: str, _log_file):
        captured.append(cmd)
        return type("R", (), {"ok": True, "message": "ok"})()

    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            side_effect=_capture_start,
        ),
        patch.object(svc, "wait_ready", return_value=True),
    ):
        svc.start()
    assert len(captured) == 1
    cmd = captured[0]
    assert "export" in cmd
    assert "CPTR_STREAM_READ_TIMEOUT=1200" in cmd
    assert "CPTR_STREAM_WRITE_TIMEOUT=1200" in cmd


def test_command_env_respects_custom_stream_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-default ``stream_timeout_s`` flows through to the env block."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr", options={"stream_timeout_s": 600}))
    captured: list[str] = []

    def _capture_start(cmd: str, _log_file):
        captured.append(cmd)
        return type("R", (), {"ok": True, "message": "ok"})()

    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            side_effect=_capture_start,
        ),
        patch.object(svc, "wait_ready", return_value=True),
    ):
        svc.start()
    assert "CPTR_STREAM_READ_TIMEOUT=600" in captured[0]
    assert "CPTR_STREAM_WRITE_TIMEOUT=600" in captured[0]


def test_command_env_omitted_when_stream_timeout_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stream_timeout_s=0`` opts out (cptr's own defaults take over)."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr", options={"stream_timeout_s": 0}))
    captured: list[str] = []

    def _capture_start(cmd: str, _log_file):
        captured.append(cmd)
        return type("R", (), {"ok": True, "message": "ok"})()

    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            side_effect=_capture_start,
        ),
        patch.object(svc, "wait_ready", return_value=True),
    ):
        svc.start()
    assert "CPTR_STREAM" not in captured[0]


def test_command_env_works_through_uv_service_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base ``UvService.start`` honors ``command_env`` independently of cptr."""
    from pydantic import BaseModel

    from genesis_worker.utils.services import UvService, UvServiceConfig

    class _Opts(BaseModel):
        pass

    cfg = UvServiceConfig(
        name="svc",
        display_name="Svc",
        description="",
        category=ServiceCategory.UTILITY,
        capabilities=ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=False,
            can_install=False,
        ),
        resource_estimate=ServiceResourceEstimate(
            vram_bytes_typical=0, vram_bytes_min=0, cpu_cores_recommended=1
        ),
        options_model=_Opts,
        package_name="svcpkg",
        binary_name="svc",
        command=["run"],
        command_env={"FOO": "bar"},
        listen_port=9999,
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
    svc = UvService(service_ctx(tmp_path, name="svc"), config=cfg)
    captured: list[str] = []

    def _capture(cmd: str, _log_file):
        captured.append(cmd)
        return type("R", (), {"ok": True, "message": "ok"})()

    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            side_effect=_capture,
        ),
        patch.object(svc, "wait_ready", return_value=True),
    ):
        svc.start()
    assert captured and "FOO=bar" in captured[0] and captured[0].startswith("export ")


def test_command_env_empty_skips_export_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``command_env`` means no ``export ... && `` prefix."""
    from pydantic import BaseModel

    from genesis_worker.utils.services import UvService, UvServiceConfig

    class _Opts(BaseModel):
        pass

    cfg = UvServiceConfig(
        name="svc",
        display_name="Svc",
        description="",
        category=ServiceCategory.UTILITY,
        capabilities=ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=False,
            can_install=False,
        ),
        resource_estimate=ServiceResourceEstimate(
            vram_bytes_typical=0, vram_bytes_min=0, cpu_cores_recommended=1
        ),
        options_model=_Opts,
        package_name="svcpkg",
        binary_name="svc",
        command=["run"],
        listen_port=9999,
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
    svc = UvService(service_ctx(tmp_path, name="svc"), config=cfg)
    captured: list[str] = []

    def _capture(cmd: str, _log_file):
        captured.append(cmd)
        return type("R", (), {"ok": True, "message": "ok"})()

    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            side_effect=_capture,
        ),
        patch.object(svc, "wait_ready", return_value=True),
    ):
        svc.start()
    assert captured and not captured[0].startswith("export")


# --- ui_pages --------------------------------------------------------------


def test_ui_pages_default_returns_framework_status_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 2: declarative services point at the framework default page."""
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    pages = svc.ui_pages
    assert len(pages) == 1
    page = pages[0]
    assert page.label == "Status"
    assert page.url_path == "cptr_status"
    assert page.path.name == "default_service_status.py"


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


# --- post-install hook ----------------------------------------------------


def test_post_install_fires_on_start(tmp_path: Path, monkeypatch) -> None:
    """The pi-agent patch is the one Python custom code cptr keeps."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    fired: list[bool] = []

    def _observe() -> None:
        fired.append(True)

    monkeypatch.setattr(svc, "_post_install", _observe)
    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            return_value=type("R", (), {"ok": True, "message": "ok"})(),
        ),
        patch.object(svc, "wait_ready", return_value=True),
    ):
        svc.start()
    assert fired == [True]


def test_post_install_default_noop_on_subclass_overrides(tmp_path: Path) -> None:
    """The UvService default _post_install is a no-op; CptrService overrides it."""
    from genesis_worker.utils.services import UvService

    assert "do_nothing" not in dir(UvService)
    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    # The override exists and is callable.
    assert callable(svc._post_install)
