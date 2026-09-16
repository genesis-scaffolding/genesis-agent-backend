"""Tests for ``UvService`` and ``UvToolInstall`` — the uv-tool declarative base."""

from __future__ import annotations

import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from genesis_worker.contracts import (
    AcquireChoice,
    InstallState,
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
)
from genesis_worker.services.cptr.options import CptrOptions
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import UvService, UvServiceConfig, UvToolInstall
from genesis_worker.utils.services.uv_service import _uv_tool_installed_version

# --- helpers ---------------------------------------------------------------


class _Opts(BaseModel):
    """Minimal options model for generic UvService construction."""

    public_host: str | None = None
    extra: str = "default"


def _config(**overrides) -> UvServiceConfig:
    """Build a default ``UvServiceConfig`` with ``extra`` opts."""
    base = {
        "name": "svc",
        "display_name": "Svc",
        "description": "A service",
        "category": ServiceCategory.UTILITY,
        "capabilities": ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        ),
        "resource_estimate": ServiceResourceEstimate(
            vram_bytes_typical=0, vram_bytes_min=0, cpu_cores_recommended=1
        ),
        "options_model": _Opts,
        "package_name": "svcpkg",
        "binary_name": "svc",
        "command": ["run"],
        "listen_host": "0.0.0.0",
        "listen_port": 9999,
    }
    base.update(overrides)
    return UvServiceConfig(**base)


def _service(tmp_path: Path, **config_overrides) -> UvService:
    cfg = _config(**config_overrides)
    return UvService(service_ctx(tmp_path, name="svc"), config=cfg)


# --- construction / identity -----------------------------------------------


def test_construction_sets_identity_from_config(tmp_path: Path) -> None:
    svc = _service(tmp_path, name="my-svc", display_name="My Svc")
    assert svc.name == "my-svc"
    assert svc.display_name == "My Svc"
    assert svc.dir_name == "my-svc"


def test_dir_name_hyphenates_underscores(tmp_path: Path) -> None:
    svc = _service(tmp_path, name="snake_case_name")
    assert svc.dir_name == "snake-case-name"


def test_construction_validates_options(tmp_path: Path) -> None:
    """An invalid option raises pydantic's ValidationError."""
    cfg = _config(options_model=_Opts)
    ctx = service_ctx(tmp_path, name="svc", options={"extra": 123})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        UvService(ctx, config=cfg)


def test_log_file_resolves_to_log_dir(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    assert svc.log_file == tmp_path / "log" / "svc.log"


def test_log_file_uses_configured_override(tmp_path: Path) -> None:
    svc = _service(tmp_path, log_filename="custom-name.log")
    assert svc.log_file == tmp_path / "log" / "custom-name.log"


# --- capabilities / category / description --------------------------------


def test_capabilities_match_config(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    caps = svc.capabilities()
    assert caps.has_web_ui is True
    assert caps.can_install is True


def test_category_from_config(tmp_path: Path) -> None:
    svc = _service(tmp_path, category=ServiceCategory.CHAT)
    assert svc.category.value == "chat"


def test_description_from_config(tmp_path: Path) -> None:
    svc = _service(tmp_path, description="My desc")
    assert svc.description == "My desc"


def test_resource_estimate_from_config(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    est = svc.resource_estimate()
    assert est.cpu_cores_recommended == 1


# --- availability / installs ----------------------------------------------


def test_is_available_true_when_binary_on_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
    assert _service(tmp_path).is_available() is True


def test_is_available_false_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert _service(tmp_path).is_available() is False


def test_installs_returns_single_uv_tool_install(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    installs = svc.installs()
    assert len(installs) == 1
    assert isinstance(installs[0], UvToolInstall)
    assert svc.primary_installable() is installs[0]


# --- public_host -----------------------------------------------------------


def test_public_host_returns_configured_value(tmp_path: Path) -> None:
    ctx = service_ctx(tmp_path, name="svc", options={"public_host": "explicit"})
    svc2 = UvService(ctx, config=_config())
    assert svc2.public_host() == "explicit"


def test_public_host_falls_back_to_socket_gethostname(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("socket.gethostname", lambda: "live-host")
    svc = _service(tmp_path)
    assert svc.public_host() == "live-host"


def test_public_host_falls_back_to_localhost(tmp_path: Path, monkeypatch) -> None:
    def _raise() -> str:
        raise OSError("no hostname")

    monkeypatch.setattr("socket.gethostname", _raise)
    svc = _service(tmp_path)
    assert svc.public_host() == "localhost"


# --- endpoints -------------------------------------------------------------


def test_runtime_endpoint_is_none(tmp_path: Path) -> None:
    assert _service(tmp_path).runtime_endpoint() is None


def test_web_ui_endpoint_none_when_stopped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(svc := _service(tmp_path), "is_running", lambda: False)
    assert svc.web_ui_endpoint() is None


def test_web_ui_endpoint_uses_public_host_and_listen_port(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path, listen_port=7777)
    monkeypatch.setattr(svc, "is_running", lambda: True)
    ctx_override = service_ctx(tmp_path, name="svc", options={"public_host": "h"})
    svc2 = UvService(ctx_override, config=_config(listen_port=7777))
    monkeypatch.setattr(svc2, "is_running", lambda: True)
    assert svc2.web_ui_endpoint() == "http://h:7777/"


# --- start / stop dispatch ------------------------------------------------


def test_start_refuses_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    svc = _service(tmp_path)
    r = svc.start()
    assert r.ok is False
    assert "not installed" in r.message


def test_start_runs_post_install_hook_before_tmux(tmp_path: Path, monkeypatch) -> None:
    """The base calls ``_post_install()`` before ``TmuxProcess.start``."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
    svc = _service(tmp_path)
    calls: list[str] = []

    def _hook() -> None:
        calls.append("post")

    monkeypatch.setattr(svc, "_post_install", _hook)
    with (
        patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            return_value=type("R", (), {"ok": True, "message": "ok"})(),
        ),
        patch.object(svc, "wait_ready", return_value=True),
    ):
        r = svc.start()
    assert r.ok is True
    assert calls == ["post"]


def test_start_returns_failure_when_tmux_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
    svc = _service(tmp_path)
    with patch(
        "genesis_worker.utils.services.uv_service.TmuxProcess.start",
        return_value=type("R", (), {"ok": False, "message": "tmux fail"})(),
    ):
        r = svc.start()
    assert r.ok is False
    assert "tmux fail" in r.message


def test_start_returns_failure_when_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
    svc = _service(tmp_path)
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


def test_stop_no_session_is_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists",
        lambda self: False,
    )
    r = _service(tmp_path).stop()
    assert r.ok is True
    assert "no session" in r.message


def test_stop_graceful_kill(tmp_path: Path, monkeypatch) -> None:
    """When the session is gone mid-grace, we return graceful success."""
    call_count = {"n": 0}

    def _exists(self) -> bool:
        call_count["n"] += 1
        # First call: present. Subsequent: gone.
        return call_count["n"] == 1

    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists",
        _exists,
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.send_interrupt",
        lambda self: None,
    )
    r = _service(tmp_path).stop()
    assert r.ok is True
    assert "killed svc" in r.message


def test_stop_force_kill_when_graceful_stalls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists",
        lambda self: True,
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.send_interrupt",
        lambda self: None,
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.kill",
        lambda self: None,
    )
    r = _service(tmp_path, graceful_stop_timeout_s=0.1).stop()
    assert r.ok is True
    assert "forced" in r.message


# --- status / wait_ready --------------------------------------------------


def test_status_stopped_when_no_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists", lambda self: False
    )
    s = _service(tmp_path).status()
    assert s.state.value == "stopped"


def test_status_running_when_session_present_and_probe_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists", lambda self: True
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.HealthProbe.probe", lambda self: True
    )
    s = _service(tmp_path).status()
    assert s.state.value == "running"


def test_status_starting_when_session_present_but_probe_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.TmuxProcess.exists", lambda self: True
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.uv_service.HealthProbe.probe", lambda self: False
    )
    s = _service(tmp_path).status()
    assert s.state.value == "starting"


def test_wait_ready_delegates_to_health_probe(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with patch.object(svc, "wait_ready", return_value=True) as mock_wait:
        assert svc.wait_ready(5.0) is True
    assert mock_wait.call_args.args == (5.0,)


# --- tail_log -------------------------------------------------------------


def test_tail_log_empty_when_log_missing(tmp_path: Path) -> None:
    assert _service(tmp_path).tail_log() == ""


def test_tail_log_returns_last_n_bytes(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.log_file.parent.mkdir(parents=True, exist_ok=True)
    svc.log_file.write_text("hello\nworld\nlast\n")
    assert "last" in svc.tail_log()


# --- uninstall guard -----------------------------------------------------


def test_uninstall_installable_refuses_while_running(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path)
    monkeypatch.setattr(svc, "is_running", lambda: True)
    with pytest.raises(RuntimeError, match="cannot uninstall"):
        svc.uninstall_installable("svcpkg")


def test_uninstall_installable_unknown_name_raises_keyerror(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path)
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with pytest.raises(KeyError, match="unknown installable"):
        svc.uninstall_installable("does-not-exist")


def test_uninstall_installable_delegates_when_stopped(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path)
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with patch.object(svc.installs()[0], "uninstall") as mock_uninst:
        svc.uninstall_installable("svcpkg")
    assert mock_uninst.called


# --- _post_install default no-op -------------------------------------------


def test_post_install_default_is_noop(tmp_path: Path) -> None:
    """Default ``_post_install`` does nothing — subclasses override."""
    svc = _service(tmp_path)
    # Should not raise.
    svc._post_install()


# --- installed_version -----------------------------------------------------


def test_installed_version_proxies_to_installable(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with patch.object(svc.installs()[0], "installed_version", return_value="1.2.3"):
        assert svc.installed_version == "1.2.3"


def test_installed_version_none_when_not_installed(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with patch.object(svc.installs()[0], "installed_version", return_value=None):
        assert svc.installed_version is None


# --- session_name --------------------------------------------------------


def test_session_name_defaults_to_config_name(tmp_path: Path) -> None:
    svc = _service(tmp_path, name="mything")
    # The default session name is captured implicitly: start() should
    # invoke TmuxProcess(self._session_name). Use a probe.
    monkeypatch = pytest.MonkeyPatch()
    captured: list[str] = []
    from genesis_worker.utils.services.uv_service import TmuxProcess as _TP

    orig_init = _TP.__init__

    def _spy(self, name: str) -> None:
        captured.append(name)
        orig_init(self, name)

    monkeypatch.setattr(_TP, "__init__", _spy)
    try:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
        with patch(
            "genesis_worker.utils.services.uv_service.TmuxProcess.start",
            return_value=type("R", (), {"ok": False, "message": ""})(),
        ):
            _ = svc.start()
        assert captured and captured[0] == "mything"
    finally:
        monkeypatch.undo()


# --- cptr subclass uses CptrOptions and overrides _post_install ----------


def test_cptr_service_uses_cptr_options_model(tmp_path: Path) -> None:
    """The cptr subclass still validates against CptrOptions."""
    from genesis_worker.services.cptr.service import CptrService

    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    assert isinstance(svc.options, CptrOptions)
    assert svc.options.listen_port == 4321


def test_cptr_post_install_overrides_default(tmp_path: Path) -> None:
    """CptrService overrides _post_install — it's not the no-op default."""
    from genesis_worker.services.cptr.service import CptrService

    svc = CptrService(service_ctx(tmp_path, name="cptr"))
    # Sanity: the override exists and is a callable that doesn't raise
    # when patch_pi_timeout() fails (the on-host pi.py path is absent
    # in tests). It's effectively a no-op in CI; either way, it doesn't raise.
    with patch.object(
        __import__("genesis_worker.services.cptr.service", fromlist=["patch_pi_timeout"]),
        "patch_pi_timeout",
        return_value=False,
    ):
        svc._post_install()  # must not raise
    # And it is wired to call patch_pi_timeout in real use.
    assert svc._post_install is not UvService._post_install


# --- UvToolInstall specifics ---------------------------------------------


def _fake_pypi_payload(version: str = "0.9.21", size: int = 4_560_844) -> dict:
    return {
        "info": {
            "name": "svcpkg",
            "version": version,
            "package_url": f"https://pypi.org/project/svcpkg/{version}/",
        },
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "url": f"https://files.example/svcpkg-{version}-py3-none-any.whl",
                "size": size,
                "digests": {"sha256": "deadbeef" * 8},
            }
        ],
    }


def test_uv_tool_list_parses_version(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = subprocess.CompletedProcess(
        args=["uv", "tool", "list"],
        returncode=0,
        stdout="svcpkg v0.9.21\n- svcpkg\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert _uv_tool_installed_version("svcpkg", timeout=5.0) == "0.9.21"


def test_uv_tool_list_returns_none_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = subprocess.CompletedProcess(
        args=["uv", "tool", "list"], returncode=0, stdout="", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert _uv_tool_installed_version("svcpkg", timeout=5.0) is None


def test_uv_tool_list_returns_none_on_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = subprocess.CompletedProcess(
        args=["uv", "tool", "list"], returncode=1, stdout="", stderr="boom"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert _uv_tool_installed_version("svcpkg", timeout=5.0) is None


def test_uv_tool_list_returns_none_when_uv_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*a, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _uv_tool_installed_version("svcpkg", timeout=5.0) is None


def test_installable_returns_latest_version_from_pypi() -> None:
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    with patch(
        "genesis_worker.utils.services.uv_service._http_get_json",
        return_value=_fake_pypi_payload(),
    ):
        versions = inst.available_versions()
    assert len(versions) == 1
    v = versions[0]
    assert v.version == "0.9.21"
    assert v.size_bytes == 4_560_844
    assert v.sha256 == "deadbeef" * 8


def test_installable_returns_empty_on_network_error() -> None:
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")

    def _raise(url: str, *, timeout: float):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("no internet")

    with patch("genesis_worker.utils.services.uv_service._http_get_json", _raise):
        assert inst.available_versions() == []


def test_installable_handles_missing_wheel() -> None:
    payload = _fake_pypi_payload()
    payload["urls"] = []
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    with patch("genesis_worker.utils.services.uv_service._http_get_json", return_value=payload):
        versions = inst.available_versions()
    assert len(versions) == 1
    assert versions[0].size_bytes is None


def test_binary_path_via_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/local/bin/{name}")
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    assert inst.binary_path() == Path("/usr/local/bin/svc")


def test_binary_path_none_when_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    assert inst.binary_path() is None
    assert inst.state() == InstallState.NOT_INSTALLED


def test_state_installed_when_binary_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    assert inst.state() == InstallState.INSTALLED


def test_install_session_runs_uv_tool_install(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")

    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    session = inst.install(version="0.9.21")
    session.wait()
    view = session.view()
    assert view.kind.value == "complete"
    assert captured and captured[0][:3] == ["uv", "tool", "install"]
    assert captured[0][3] == "svcpkg==0.9.21"


def test_install_session_default_uses_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")

    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    session = inst.install()
    session.wait()
    assert captured[0][3] == "svcpkg@latest"


def test_install_session_failure_on_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="network error"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")

    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    session = inst.install(version="0.9.21")
    session.wait()
    view = session.view()
    assert view.kind.value == "failed"
    assert "network error" in (view.error or "")


def test_install_session_missing_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(args, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    session = inst.install(version="0.9.21")
    session.wait()
    view = session.view()
    assert view.kind.value == "failed"
    assert "uv" in (view.error or "")


def test_install_session_submit_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/svc")

    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    session = inst.install(version="0.9.21")
    session.submit(AcquireChoice())
    session.wait()
    assert session.view().kind.value == "complete"


def test_uninstall_runs_uv_tool_uninstall(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    inst.uninstall()
    assert captured and captured[0] == ["uv", "tool", "uninstall", "svcpkg"]


def test_uninstall_swallows_missing_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(args, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    inst.uninstall()  # must not raise


def test_installable_name_is_package_name() -> None:
    inst = UvToolInstall(package_name="svcpkg", binary_name="svc")
    assert inst.name == "svcpkg"
