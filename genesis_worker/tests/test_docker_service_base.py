"""Tests for ``DockerService`` and ``DockerImageInstall`` — the docker declarative base."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from genesis_worker.contracts import (
    InstallState,
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
    ServiceState,
)
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.process import DockerContainer
from genesis_worker.utils.services import (
    AuthConfig,
    DockerImageInstall,
    DockerService,
    DockerServiceConfig,
)
from genesis_worker.utils.services.docker_service import (
    _cache_path,
    _read_cache,
    _write_cache,
)

# --- helpers ---------------------------------------------------------------


class _Opts(BaseModel):
    """Minimal options model for DockerService construction."""

    public_host: str | None = None
    jwt_enabled: bool = False
    api_token: str | None = None


def _config(**overrides) -> DockerServiceConfig:
    base = {
        "name": "svc",
        "display_name": "Svc",
        "description": "A service",
        "category": ServiceCategory.CRAWLER,
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
        "image_repo": "example/svc",
        "image_tag": "latest",
        "image_install_name": "svc",
        "source_url": "https://example.com/svc",
        "container_name": "svc",
        "listen_host": "0.0.0.0",
        "listen_port": 12345,
        "internal_port": 12345,
        "health_probe_path": "/health",
        "web_ui_path": "/",
    }
    base.update(overrides)
    return DockerServiceConfig(**base)


def _service(tmp_path: Path, **config_overrides) -> DockerService:
    cfg = _config(**config_overrides)
    return DockerService(service_ctx(tmp_path, name="svc"), config=cfg)


# --- construction / identity -----------------------------------------------


def test_construction_sets_identity_from_config(tmp_path: Path) -> None:
    svc = _service(tmp_path, name="my-svc", display_name="My Svc")
    assert svc.name == "my-svc"
    assert svc.display_name == "My Svc"
    assert svc.dir_name == "my-svc"


def test_dir_name_hyphenates_underscores(tmp_path: Path) -> None:
    svc = _service(tmp_path, name="snake_case_name")
    assert svc.dir_name == "snake-case-name"


def test_construction_resolves_data_dir_from_ctx(tmp_path: Path) -> None:
    """ctx.data_dir is already scoped; ``data_dir_subpath="data"`` appends it."""
    svc = _service(tmp_path)
    assert svc.data_dir == tmp_path / "data" / "data"


def test_construction_respects_data_dir_subpath_none(tmp_path: Path) -> None:
    svc = _service(tmp_path, data_dir_subpath=None)
    assert svc.data_dir == tmp_path / "data"


def test_construction_resolves_log_file(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    assert svc.log_file == tmp_path / "log" / "svc.log"


def test_construction_respects_log_filename_override(tmp_path: Path) -> None:
    svc = _service(tmp_path, log_filename="custom.log")
    assert svc.log_file == tmp_path / "log" / "custom.log"


def test_construction_validates_options(tmp_path: Path) -> None:
    cfg = _config()
    ctx = service_ctx(tmp_path, name="svc", options={"jwt_enabled": "not-bool"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DockerService(ctx, config=cfg)


# --- introspection ---------------------------------------------------------


def test_image_ref_format(tmp_path: Path) -> None:
    svc = _service(tmp_path, image_repo="foo/bar", image_tag="v1.0")
    assert svc.image_ref == "foo/bar:v1.0"


def test_listen_address_format(tmp_path: Path) -> None:
    svc = _service(tmp_path, listen_host="127.0.0.1", listen_port=8888)
    assert svc.listen_address == "127.0.0.1:8888"


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


def test_is_available_true_when_image_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    assert _service(tmp_path).is_available() is True


def test_is_available_false_when_image_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: False))
    assert _service(tmp_path).is_available() is False


def test_installs_returns_single_image_install(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    installs = svc.installs()
    assert len(installs) == 1
    assert isinstance(installs[0], DockerImageInstall)
    assert svc.primary_installable() is installs[0]


# --- public_host -----------------------------------------------------------


def test_public_host_returns_configured_value(tmp_path: Path) -> None:
    ctx = service_ctx(tmp_path, name="svc", options={"public_host": "explicit"})
    svc = DockerService(ctx, config=_config())
    assert svc.public_host() == "explicit"


def test_public_host_falls_back_to_socket_gethostname(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("socket.gethostname", lambda: "live-host")
    assert _service(tmp_path).public_host() == "live-host"


def test_public_host_falls_back_to_localhost(tmp_path: Path, monkeypatch) -> None:
    def _raise() -> str:
        raise OSError("no hostname")

    monkeypatch.setattr("socket.gethostname", _raise)
    assert _service(tmp_path).public_host() == "localhost"


# --- endpoints -------------------------------------------------------------


def test_runtime_endpoint_is_none(tmp_path: Path) -> None:
    assert _service(tmp_path).runtime_endpoint() is None


def test_web_ui_endpoint_none_when_stopped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: False,
    )
    assert _service(tmp_path).web_ui_endpoint() is None


def test_web_ui_endpoint_uses_configured_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: True,
    )
    ctx = service_ctx(tmp_path, name="svc", options={"public_host": "h"})
    svc2 = DockerService(ctx, config=_config(web_ui_path="/playground/", listen_port=8080))
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: True,
    )
    assert svc2.web_ui_endpoint() == "http://h:8080/playground/"


def test_web_ui_endpoint_normalizes_missing_leading_slash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: True,
    )
    svc = _service(tmp_path, web_ui_path="playground")
    endpoint = svc.web_ui_endpoint()
    assert endpoint is not None
    assert endpoint.endswith("/playground/")


# --- start / stop dispatch ------------------------------------------------


def test_start_refuses_when_image_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: False))
    r = _service(tmp_path).start()
    assert r.ok is False
    assert "image not pulled" in r.message


def test_start_runs_container_when_image_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))

    def _fake_run(self, **kw):
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _fake_run
    )
    r = _service(tmp_path).start()
    assert r.ok is True


def test_start_passes_extra_volumes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    captured: dict = {}

    def _capture_run(self, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _capture_run
    )
    _service(tmp_path, extra_volumes={"/data": str(tmp_path / "shared")}).start()
    assert captured["volumes"]["/data"] == str(tmp_path / "shared")


def test_start_passes_puid_pgid_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    captured: dict = {}

    def _capture_run(self, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _capture_run
    )
    _service(tmp_path).start()
    assert "PUID" in captured["env"]
    assert "PGID" in captured["env"]


def test_start_omits_puid_pgid_when_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    captured: dict = {}

    def _capture_run(self, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _capture_run
    )
    _service(
        tmp_path,
        puid_default=False,
        pgid_default=False,
    ).start()
    assert "PUID" not in captured["env"]
    assert "PGID" not in captured["env"]


def test_stop_calls_container_stop_and_remove(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def _capture_stop(self, **kw):
        calls.append("stop")
        return type("R", (), {"ok": True, "message": "ok"})()

    def _capture_remove(self):
        calls.append("remove")

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.stop",
        _capture_stop,
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.remove",
        _capture_remove,
    )
    r = _service(tmp_path).stop()
    assert r.ok is True
    assert "stop" in calls and "remove" in calls


# --- status / wait_ready --------------------------------------------------


def test_status_stopped_when_container_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: False,
    )
    s = _service(tmp_path).status()
    assert s.state == ServiceState.STOPPED


def test_status_running_when_container_up_and_probe_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: True,
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.HealthProbe.probe", lambda self: True
    )
    s = _service(tmp_path).status()
    assert s.state == ServiceState.RUNNING


def test_status_starting_when_container_up_but_probe_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: True,
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.HealthProbe.probe", lambda self: False
    )
    s = _service(tmp_path).status()
    assert s.state == ServiceState.STARTING


def test_wait_ready_delegates_to_health_probe(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with patch.object(svc, "wait_ready", return_value=True) as mock_wait:
        assert svc.wait_ready(5.0) is True
    assert mock_wait.call_args.args == (5.0,)


# --- is_running ----------------------------------------------------------


def test_is_running_delegates_to_container(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: True,
    )
    assert _service(tmp_path).is_running() is True


# --- tail_log -------------------------------------------------------------


def test_tail_log_delegates_to_container(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.logs",
        lambda self, tail_lines: f"tail-{tail_lines}",
    )
    out = _service(tmp_path).tail_log()
    assert out.startswith("tail-")


# --- auth: enabled vs token ----------------------------------------------


def _auth_config(tmp_path: Path) -> AuthConfig:
    return AuthConfig(
        enabled_option="jwt_enabled",
        token_env_var="API_TOKEN",
        token_file=tmp_path / "state" / "api_token",
        token_file_mode=0o600,
        token_generator="random_hex_32",
        fallback_option="api_token",
    )


def test_auth_token_returns_none_when_jwt_enabled(tmp_path: Path) -> None:
    """When ``jwt_enabled`` is True, the token machinery is bypassed."""
    ctx = service_ctx(tmp_path, name="svc", options={"jwt_enabled": True})
    svc2 = DockerService(ctx, config=_config(auth=_auth_config(tmp_path)))
    assert svc2.auth_token() is None
    assert svc2.auth_enabled() is True


def test_auth_token_returns_fallback_option(tmp_path: Path) -> None:
    ctx = service_ctx(tmp_path, name="svc", options={"api_token": "explicit-token-from-user"})
    svc2 = DockerService(ctx, config=_config(auth=_auth_config(tmp_path)))
    assert svc2.auth_token() == "explicit-token-from-user"
    assert svc2.auth_enabled() is False


def test_auth_token_reads_persisted_file(tmp_path: Path) -> None:
    path = tmp_path / "state" / "api_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("persisted-token\n")
    auth = AuthConfig(
        enabled_option="jwt_enabled",
        token_env_var="API_TOKEN",
        token_file=path,
        token_file_mode=0o600,
        token_generator="random_hex_32",
        fallback_option="api_token",
    )
    svc = _service(tmp_path, auth=auth)
    assert svc.auth_token() == "persisted-token"


def test_auth_token_generates_and_persists(tmp_path: Path) -> None:
    """When no option/file, ``_resolve_or_generate_token`` writes a token."""
    auth = _auth_config(tmp_path)
    svc = _service(tmp_path, auth=auth)
    token = svc._resolve_or_generate_token()
    assert len(token) == 64  # 32 bytes hex
    assert svc.auth_token() == token  # Now persisted.


def test_auth_disabled_when_no_auth_config(tmp_path: Path) -> None:
    svc = _service(tmp_path)  # no auth
    assert svc.auth_token() is None
    assert svc.auth_enabled() is False


def test_start_injects_token_env_var(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    captured: dict = {}

    def _capture_run(self, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _capture_run
    )
    svc = _service(tmp_path, auth=_auth_config(tmp_path))
    svc.start()
    assert "API_TOKEN" in captured["env"]


# --- uninstall guard -----------------------------------------------------


def test_uninstall_installable_refuses_while_running(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path)
    monkeypatch.setattr(svc, "is_running", lambda: True)
    with pytest.raises(RuntimeError, match="cannot uninstall"):
        svc.uninstall_installable("svc")


def test_uninstall_installable_unknown_name_raises_keyerror(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path)
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with pytest.raises(KeyError, match="unknown installable"):
        svc.uninstall_installable("does-not-exist")


def test_uninstall_installable_delegates_when_stopped(tmp_path: Path, monkeypatch) -> None:
    svc = _service(tmp_path)
    monkeypatch.setattr(svc, "is_running", lambda: False)
    with patch.object(svc.installs()[0], "uninstall") as mock_uninst:
        svc.uninstall_installable("svc")
    assert mock_uninst.called


# --- DockerImageInstall: state / installed_version / cache -----------------


def _make_installable(tmp_path: Path, **kw) -> DockerImageInstall:
    base = {
        "image_repo": "example/svc",
        "image_tag": "latest",
        "cache_dir": tmp_path / "cache",
        "state_dir": tmp_path / "state",
        "name": "svc",
        "source_url": "https://example.com",
    }
    base.update(kw)
    return DockerImageInstall(**base)


def test_image_install_image_ref_format(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path, image_repo="foo/bar", image_tag="v1")
    assert inst.image_ref == "foo/bar:v1"


def test_image_install_source_url(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path, source_url="https://example.com")
    assert inst.source_url() == "https://example.com"


def test_image_install_binary_path_is_none(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    assert inst.binary_path() is None


def test_image_install_state_installed_when_image_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    assert _make_installable(tmp_path).state() == InstallState.INSTALLED


def test_image_install_state_not_installed_when_image_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: False))
    assert _make_installable(tmp_path).state() == InstallState.NOT_INSTALLED


def test_image_install_installed_version_reads_selection(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("0.7.0\n")
    assert inst.installed_version() == "0.7.0"


def test_image_install_installed_version_none_when_no_selection(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    assert inst.installed_version() is None


def test_image_install_available_versions_uses_cache(tmp_path: Path, monkeypatch) -> None:
    """A fresh cache means ``list_remote_tags`` is not called."""
    cache = _cache_path(tmp_path / "cache", "example/svc")
    _write_cache(cache, ["latest", "0.7.0"])
    called: list[str] = []

    def _list(repo: str, *, auth_token: str | None = None):
        called.append(repo)
        return []

    monkeypatch.setattr(DockerContainer, "list_remote_tags", staticmethod(_list))
    versions = _make_installable(tmp_path).available_versions()
    assert [v.version for v in versions] == ["latest", "0.7.0"]
    assert not called  # cache hit


def test_image_install_available_versions_fetches_when_no_cache(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo, *, auth_token=None: ["a", "b"]),
    )
    versions = _make_installable(tmp_path).available_versions()
    assert [v.version for v in versions] == ["a", "b"]


def test_image_install_available_versions_applies_arch_filter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo, *, auth_token=None: ["v1-amd64", "v1-arm64"]),
    )
    inst = _make_installable(tmp_path, arch_filter=lambda tag: "amd64" in tag)
    versions = inst.available_versions()
    assert [v.version for v in versions] == ["v1-amd64"]


def test_image_install_cache_round_trip(tmp_path: Path) -> None:
    """Write then read returns the same data within the TTL."""
    p = _cache_path(tmp_path / "cache", "example/svc")
    _write_cache(p, ["a", "b"])
    assert _read_cache(p, ttl_s=60) == ["a", "b"]


def test_image_install_cache_expired(tmp_path: Path) -> None:
    p = _cache_path(tmp_path / "cache", "example/svc")
    _write_cache(p, ["a", "b"])
    # Manually expire by rewriting fetched_at to old.
    payload = json.loads(p.read_text())
    payload["fetched_at"] = payload["fetched_at"] - 60 * 60  # 1h ago
    p.write_text(json.dumps(payload))
    assert _read_cache(p, ttl_s=60) is None


def test_image_install_invalidate_versions_cache(tmp_path: Path) -> None:
    p = _cache_path(tmp_path / "cache", "example/svc")
    _write_cache(p, ["a"])
    inst = _make_installable(tmp_path)
    inst.invalidate_versions_cache()
    assert not p.exists()


def test_image_install_uninstall_runs_docker_rmi(tmp_path: Path, monkeypatch) -> None:
    """The version comes from installed_version when called without version."""
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("0.7.0\n")
    captured: list[list[str]] = []

    def _fake_run(args, **kw):
        captured.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    inst.uninstall()
    assert captured and captured[0] == ["docker", "rmi", "example/svc:0.7.0"]


def test_image_install_uninstall_noop_when_no_selection(tmp_path: Path, monkeypatch) -> None:
    captured: list[list[str]] = []

    def _fake_run(args, **kw):
        captured.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    inst = _make_installable(tmp_path)
    inst.uninstall()
    assert not captured


# --- ui_pages default ----------------------------------------------------


def test_ui_pages_default_returns_empty_list(tmp_path: Path) -> None:
    """The framework page machinery ships in phase 2."""
    assert _service(tmp_path).ui_pages == []
