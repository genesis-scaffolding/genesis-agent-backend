"""End-to-end test for bifrost's setup token (ADR-052).

Bifrost v2 refuses to create the first admin account unless a setup
token is configured on the server. The worker generates one before the
container starts, persists it under the state dir at 0600, injects it as
``BIFROST_SETUP_TOKEN`` and shows it on the status page with a copy
button.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.process import DockerContainer
from genesis_worker.utils.services import load_service_spec
from genesis_worker.utils.services.docker_service import DockerService

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


def _load_bifrost(tmp_path: Path) -> DockerService:
    svc = load_service_spec(
        _DECLARATIVE_DIR / "bifrost.yaml",
        ctx=service_ctx(tmp_path, name="bifrost"),
    )
    assert isinstance(svc, DockerService)
    return svc


def test_start_injects_setup_token_env_var(tmp_path: Path, monkeypatch) -> None:
    """``start()`` puts the persisted token into the container's environment."""
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    captured: dict[str, Any] = {}

    def _capture_run(self, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _capture_run
    )
    svc = _load_bifrost(tmp_path)
    svc.start()

    env = captured["env"]
    assert "BIFROST_SETUP_TOKEN" in env
    token = env["BIFROST_SETUP_TOKEN"]
    assert len(token) == 64
    # The value is the one the pre-start hook persisted.
    assert token == (tmp_path / "state" / "setup_token").read_text()
    # The auth block owns the env var — the YAML does not declare it too.
    assert "BIFROST_SETUP_TOKEN" not in svc.config.extra_env


def test_start_reuses_the_persisted_setup_token(tmp_path: Path, monkeypatch) -> None:
    """A restart injects the same token — the file is read, never rewritten."""
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    captured: list[dict[str, Any]] = []

    def _capture_run(self, **kwargs):
        captured.append(kwargs)
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _capture_run
    )
    svc = _load_bifrost(tmp_path)
    svc.start()
    first = captured[0]["env"]["BIFROST_SETUP_TOKEN"]

    svc.start()
    second = captured[1]["env"]["BIFROST_SETUP_TOKEN"]

    assert first == second
    assert (tmp_path / "state" / "setup_token").stat().st_mode & 0o777 == 0o600


def test_user_supplied_setup_token_is_injected(tmp_path: Path, monkeypatch) -> None:
    """A user-typed setup token beats the generated file."""
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    captured: dict[str, Any] = {}

    def _capture_run(self, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ok": True, "message": "started"})()

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.run", _capture_run
    )
    ctx = service_ctx(tmp_path, name="bifrost", options={"setup_token": "operator-token"})
    svc = load_service_spec(_DECLARATIVE_DIR / "bifrost.yaml", ctx=ctx)
    assert isinstance(svc, DockerService)
    svc.start()

    assert captured["env"]["BIFROST_SETUP_TOKEN"] == "operator-token"


def test_status_page_opts_into_the_auth_token_panel(tmp_path: Path) -> None:
    """The status page shows the token so the operator can copy it."""
    svc = _load_bifrost(tmp_path)
    assert "auth_token" in svc.ui_panels
    assert svc.config.auth is not None
    assert svc.config.auth.label == "Setup token"
    assert "BIFROST_SETUP_TOKEN" in svc.config.auth.usage


def test_other_specs_keep_the_default_wording(tmp_path: Path) -> None:
    """A spec that declares no display fields renders as it always did."""
    svc = load_service_spec(
        _DECLARATIVE_DIR / "crawl4ai.yaml",
        ctx=service_ctx(tmp_path, name="crawl4ai"),
    )
    assert isinstance(svc, DockerService)
    assert svc.config.auth is not None
    assert svc.config.auth.label == ""
    assert svc.config.auth.usage == ""
