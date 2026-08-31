"""Tests for the Crawl4AI service plugin (the InferenceService-shaped facade)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from genesis_worker.services.crawl4ai.service import Crawl4AiService
from genesis_worker.tests._factories import service_ctx

# --- construction ----------------------------------------------------------


def test_construction_uses_default_options(tmp_path: Path) -> None:
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.name == "crawl4ai"
    assert svc.display_name == "Crawl4AI"
    assert svc.listen_address == "0.0.0.0:11235"


def test_construction_applies_options(tmp_path: Path) -> None:
    svc = Crawl4AiService(
        service_ctx(
            tmp_path,
            name="crawl4ai",
            options={"listen_port": 9999, "image_tag": "0.7.0"},
        )
    )
    assert svc.listen_address == "0.0.0.0:9999"
    assert svc.image_ref == "unclecode/crawl4ai:0.7.0"


def test_construction_defaults_log_file_to_log_dir(tmp_path: Path) -> None:
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.log_file == tmp_path / "log" / "crawl4ai.log"


def test_construction_respects_log_file_option(tmp_path: Path) -> None:
    custom = tmp_path / "my.log"
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai", options={"log_file": str(custom)}))
    assert svc.log_file == custom


def test_construction_data_dir_defaults_under_data_dir(tmp_path: Path) -> None:
    """ctx.data_dir is already scoped to the service by the framework."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc._data_path == tmp_path / "data" / "data"


def test_construction_data_dir_respects_option(tmp_path: Path) -> None:
    custom = tmp_path / "custom_data"
    svc = Crawl4AiService(
        service_ctx(tmp_path, name="crawl4ai", options={"data_path": str(custom)})
    )
    assert svc._data_path == custom


def test_construction_puid_pgid_auto_default(tmp_path: Path) -> None:
    """PUID/PGID default to ``os.getuid``/``os.getgid`` when not supplied."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc._puid == os.getuid()
    assert svc._pgid == os.getgid()


def test_construction_puid_pgid_overrides(tmp_path: Path) -> None:
    svc = Crawl4AiService(
        service_ctx(tmp_path, name="crawl4ai", options={"puid": 1234, "pgid": 5678})
    )
    assert svc._puid == 1234
    assert svc._pgid == 5678


# --- capabilities / resource estimate --------------------------------------


def test_capabilities_match_design() -> None:
    """Web UI + install only; no inference / image / training axes."""
    svc = Crawl4AiService(service_ctx(Path("/tmp"), name="crawl4ai"))
    caps = svc.capabilities()
    assert caps.has_web_ui is True
    assert caps.can_install is True
    assert caps.can_generate_config is False
    assert caps.can_export_for_agent is False
    assert caps.can_serve_llm is False
    assert caps.can_serve_image is False
    assert caps.can_train_models is False


def test_resource_estimate_has_no_gpu() -> None:
    svc = Crawl4AiService(service_ctx(Path("/tmp"), name="crawl4ai"))
    est = svc.resource_estimate()
    assert est.vram_bytes_typical == 0
    assert est.vram_bytes_min == 0
    assert est.cpu_cores_recommended >= 1


# --- public_host -----------------------------------------------------------


def test_public_host_falls_back_to_socket_hostname(tmp_path: Path) -> None:
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    # Whatever the host reports; the point is no exception.
    assert isinstance(svc.public_host(), str)
    assert svc.public_host() != ""


def test_public_host_respects_option(tmp_path: Path) -> None:
    svc = Crawl4AiService(
        service_ctx(tmp_path, name="crawl4ai", options={"public_host": "crawl.example.com"})
    )
    assert svc.public_host() == "crawl.example.com"


# --- ui_pages --------------------------------------------------------------


def test_ui_pages_contain_status_and_image(tmp_path: Path) -> None:
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    paths = [p.path for p in svc.ui_pages]
    assert any(p.name == "status.py" for p in paths)
    assert any(p.name == "image.py" for p in paths)


def test_ui_pages_first_is_status(tmp_path: Path) -> None:
    """First entry is the landing page (ADR-010)."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.ui_pages[0].path.name == "status.py"


def test_ui_pages_have_unique_url_paths(tmp_path: Path) -> None:
    """Each UiPage needs an explicit slug to avoid Streamlit collisions."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    urls = [p.url_path for p in svc.ui_pages]
    assert len(set(urls)) == len(urls)
    assert "crawl4ai_status" in urls
    assert "crawl4ai_image" in urls


# --- install axis ----------------------------------------------------------


def test_installs_returns_one(tmp_path: Path) -> None:
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    installs = svc.installs()
    assert len(installs) == 1
    assert installs[0].name == "crawl4ai"


def test_primary_installable_matches(tmp_path: Path) -> None:
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.primary_installable() is svc.installs()[0]


def test_uninstall_installable_refuses_when_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uninstall is blocked while the container is running."""
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: True,
    )
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    with pytest.raises(RuntimeError, match="stop the service first"):
        svc.uninstall_installable("crawl4ai")


def test_uninstall_installable_unknown_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown name -> KeyError. Mock is_running so we don't hit the host daemon."""
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: False,
    )
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    with pytest.raises(KeyError, match="unknown installable"):
        svc.uninstall_installable("nope")


# --- is_available ---------------------------------------------------------


def test_is_available_consults_install_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.Crawl4AiImage.state",
        lambda self: (
            __import__(
                "genesis_worker.contracts.install", fromlist=["InstallState"]
            ).InstallState.INSTALLED
        ),
    )
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.is_available() is True


def test_is_available_false_when_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.Crawl4AiImage.state",
        lambda self: (
            __import__(
                "genesis_worker.contracts.install", fromlist=["InstallState"]
            ).InstallState.NOT_INSTALLED
        ),
    )
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.is_available() is False


# --- runtime_endpoint -----------------------------------------------------


def test_runtime_endpoint_is_none(tmp_path: Path) -> None:
    """Crawl4AI is not an inference backend; no OpenAI-shaped endpoint."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.runtime_endpoint() is None


def test_web_ui_endpoint_none_when_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: False,
    )
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.web_ui_endpoint() is None


def test_web_ui_endpoint_when_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: True,
    )
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    # The dashboard lives at /playground/, not / (which is auth-gated).
    assert svc.web_ui_endpoint() == "http://" + svc.public_host() + ":11235/playground/"


# --- auth: env composition + token persistence ----------------------------


def _captured_start_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Run ``service.start()`` with lifecycle mocked; return what was passed."""
    captured: dict = {}

    def _fake_start(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        from genesis_worker.contracts import StartResult

        return StartResult(ok=True, message="started")

    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.start_crawl4ai",
        _fake_start,
    )
    return captured


def test_start_generates_and_persists_api_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First start: no token file -> generate, persist with 0o600, pass to container."""
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: False,
    )
    captured = _captured_start_kwargs(monkeypatch)

    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    result = svc.start()
    assert result.ok is True

    env = captured["env"]
    token = env["CRAWL4AI_API_TOKEN"]
    # 64 hex chars = 256 bits.
    assert len(token) == 64
    int(token, 16)  # parses as hex

    token_path = svc._api_token_path
    assert token_path.is_file()
    assert token_path.read_text().strip() == token
    # Mode 0o600: only the host user can read it.
    assert (token_path.stat().st_mode & 0o777) == 0o600


def test_start_reuses_persisted_api_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing token file -> read it, pass to container, don't regenerate."""
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: False,
    )
    captured = _captured_start_kwargs(monkeypatch)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    existing = "a" * 64
    (state_dir / "api_token").write_text(existing + "\n")

    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    svc.start()

    assert captured["env"]["CRAWL4AI_API_TOKEN"] == existing
    # File content unchanged (no overwrite).
    assert (svc._api_token_path.read_text().strip()) == existing


def test_start_uses_explicit_api_token_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit api_token option wins; file is not read or written."""
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: False,
    )
    captured = _captured_start_kwargs(monkeypatch)

    # Even with an existing file, the option must win.
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "api_token").write_text("from-file")

    svc = Crawl4AiService(
        service_ctx(
            tmp_path,
            name="crawl4ai",
            options={"api_token": "from-option"},
        )
    )
    svc.start()

    assert captured["env"]["CRAWL4AI_API_TOKEN"] == "from-option"
    # File untouched.
    assert svc._api_token_path.read_text().strip() == "from-file"


def test_start_uses_jwt_enabled_instead_of_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jwt_enabled=True sets CRAWL4AI_JWT_ENABLED and skips the token path."""
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: False,
    )
    captured = _captured_start_kwargs(monkeypatch)

    svc = Crawl4AiService(
        service_ctx(
            tmp_path,
            name="crawl4ai",
            options={"jwt_enabled": True},
        )
    )
    svc.start()

    env = captured["env"]
    assert env["CRAWL4AI_JWT_ENABLED"] == "true"
    assert "CRAWL4AI_API_TOKEN" not in env
    # No token file written.
    assert not svc._api_token_path.exists()


def test_start_jwt_wins_over_explicit_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both are set, jwt_enabled wins; the explicit token is ignored."""
    monkeypatch.setattr(
        "genesis_worker.services.crawl4ai.service.lifecycle.is_running_crawl4ai",
        lambda _name: False,
    )
    captured = _captured_start_kwargs(monkeypatch)

    svc = Crawl4AiService(
        service_ctx(
            tmp_path,
            name="crawl4ai",
            options={"api_token": "ignored", "jwt_enabled": True},
        )
    )
    svc.start()

    env = captured["env"]
    assert env["CRAWL4AI_JWT_ENABLED"] == "true"
    assert "CRAWL4AI_API_TOKEN" not in env


# --- public api_token() — read-only, no generation --------------------------


def test_api_token_returns_none_when_nothing_set(tmp_path: Path) -> None:
    """No option, no file -> None. The service hasn't been started yet."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.api_token() is None


def test_api_token_returns_none_in_jwt_mode(tmp_path: Path) -> None:
    """jwt_enabled=True means the token is irrelevant; don't surface one."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai", options={"jwt_enabled": True}))
    assert svc.api_token() is None


def test_api_token_returns_explicit_option(tmp_path: Path) -> None:
    """Explicit api_token option wins, even when a file exists."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "api_token").write_text("from-file")
    svc = Crawl4AiService(
        service_ctx(tmp_path, name="crawl4ai", options={"api_token": "from-option"})
    )
    assert svc.api_token() == "from-option"


def test_api_token_reads_persisted_file(tmp_path: Path) -> None:
    """No option, file present -> read from file."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "api_token").write_text("abcdef" * 10 + "\n")
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.api_token() == "abcdef" * 10


def test_api_token_does_not_generate(tmp_path: Path) -> None:
    """api_token() never writes to disk; that's start()'s job."""
    svc = Crawl4AiService(service_ctx(tmp_path, name="crawl4ai"))
    assert svc.api_token() is None
    assert not svc._api_token_path.exists()
    # Calling again still doesn't generate.
    assert svc.api_token() is None
    assert not svc._api_token_path.exists()
