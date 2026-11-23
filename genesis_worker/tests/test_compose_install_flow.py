"""The multi-image compose install flow — the regression guard for ADR-049.

Gen's PR-gate report: on a clean node the Status page's Install button
pulled ONE image of paperless-ngx's three-image stack, the outcome of the
install was never shown, and the page went on offering Install instead of
Start — so the stack could not be started without hunting the companion
images on the Images page.

These tests drive the real Status page (Streamlit's ``AppTest``) and the
real compose service against the real paperless spec. Docker is
monkeypatched away: no network, no daemon, no images.

The loader guard (F5) has its own tests in ``test_declarative_specs.py``;
the one here is the install-axis angle of the same rule.
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from genesis_worker import GenesisWorker
from genesis_worker.contracts import (
    AcquireChoice,
    AcquireSession,
    AcquireState,
    AcquireStateKind,
    AcquireView,
    StartResult,
)
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.utils.process import DockerContainer
from genesis_worker.utils.services import ComposeService, load_service_spec
from genesis_worker.utils.services.compose_stack import DockerComposeStack
from genesis_worker.utils.services.docker_service import DockerImageInstall

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"
_SESSION_KEY = "status-paperless-install/session"
_TERMINAL = (AcquireStateKind.COMPLETE, AcquireStateKind.FAILED, AcquireStateKind.CANCELLED)


# --- helpers ----------------------------------------------------------------


class _StubSession:
    """An acquire session that is already in a terminal state."""

    def __init__(
        self,
        kind: AcquireStateKind = AcquireStateKind.COMPLETE,
        *,
        title: str = "done",
        error: str | None = None,
    ) -> None:
        self._kind = kind
        self._title = title
        self._error = error

    @property
    def repo_id(self) -> str:
        return "stub/repo"

    @property
    def state(self) -> AcquireState:
        return AcquireState(kind=self._kind, repo_id="stub/repo")

    def view(self) -> AcquireView:
        return AcquireView(
            kind=self._kind,
            title=self._title,
            error=self._error,
            can_cancel=False,
        )

    def submit(self, choice: AcquireChoice) -> None:
        return None

    def cancel(self) -> None:
        return None

    def wait(self) -> AcquireView:
        return self.view()


def _make_worker(tmp_path: Path) -> GenesisWorker:
    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )
    return GenesisWorker(settings=settings)


def _render_status(worker: GenesisWorker, name: str, tmp_path: Path) -> AppTest:
    """Drive the Status page's service_info panel against a real worker."""
    body = textwrap.dedent(
        f"""
        import streamlit as st
        from genesis_worker.utils.services import panels

        worker = st.session_state["worker"]
        svc = worker.service("{name}")
        panels.render(svc, ["service_info"], {{}})
        """
    )
    driver = tmp_path / "_status_driver.py"
    driver.write_text(body)
    at = AppTest.from_file(str(driver), default_timeout=30)
    at.session_state["worker"] = worker
    return at


def _patch_docker(monkeypatch: pytest.MonkeyPatch, *, present: bool = False) -> dict[str, Any]:
    """Hermetic docker state. Returns the install call log."""
    calls: dict[str, Any] = {"install": [], "uninstall": []}

    def _install(self: Any, *, version: str | None = None) -> Any:
        calls["install"].append((self.name, version))
        return _StubSession()

    monkeypatch.setattr(DockerImageInstall, "install", _install)
    monkeypatch.setattr(DockerImageInstall, "available_versions", lambda self: [])
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: present))
    return calls


def _wait_for_terminal(at: AppTest, timeout_s: float = 10.0) -> AcquireSession:
    """Block until the install session in the page's session state is terminal."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _SESSION_KEY in at.session_state:
            session = at.session_state[_SESSION_KEY]
            if session.state.kind in _TERMINAL:
                return session
        time.sleep(0.01)
    raise AssertionError("the install session never reached a terminal state")


def _button_labels(at: AppTest) -> list[str]:
    return [b.label for b in at.button]


# --- the clean-node flow ----------------------------------------------------


def test_clean_node_status_page_install_pulls_every_image_of_the_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Status page's Install button fetches the WHOLE stack.

    This is the test that would have caught Gen's failure: with no image
    present, clicking Install pulled one image of three, the service stayed
    unavailable, and the page never offered Start.
    """
    worker = _make_worker(tmp_path)
    calls = _patch_docker(monkeypatch, present=False)
    at = _render_status(worker, "paperless", tmp_path)
    at.run()
    assert not at.exception, f"page crashed: {at.exception}"

    # Not available → Install, never Start.
    assert _button_labels(at) == ["Install"]

    at.button(key="status-paperless-install-install").click()
    at.run()
    assert not at.exception, f"page crashed: {at.exception}"
    _wait_for_terminal(at)

    # Every image of the stack was pulled — not just the first one.
    assert calls["install"] == [
        ("paperless:paperless", None),
        ("paperless:paperless-db", None),
        ("paperless:paperless-broker", None),
    ]

    # With the images on disk the page offers Start.
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: True))
    at.run()
    assert not at.exception, f"page crashed: {at.exception}"
    assert "Start" in _button_labels(at)
    assert worker.service("paperless").is_available() is True


def test_install_failure_is_shown_and_survives_a_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed pull is named on the page — and stays there.

    The defect this guards: the outcome was rendered into a run that was
    immediately discarded, so a failure looked exactly like a no-op.
    """
    worker = _make_worker(tmp_path)

    def _install(self: Any, *, version: str | None = None) -> Any:
        if self.name == "paperless:paperless-db":
            return _StubSession(
                AcquireStateKind.FAILED,
                title="Failed: postgres:18-alpine",
                error="RuntimeError: docker pull postgres:18-alpine failed (rc=1): no such image",
            )
        return _StubSession()

    monkeypatch.setattr(DockerImageInstall, "install", _install)
    monkeypatch.setattr(DockerImageInstall, "available_versions", lambda self: [])
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: False))

    at = _render_status(worker, "paperless", tmp_path)
    at.run()
    at.button(key="status-paperless-install-install").click()
    at.run()
    _wait_for_terminal(at)
    at.run()
    assert not at.exception, f"page crashed: {at.exception}"

    errors = [e.value for e in at.error]
    assert errors, "the failure was not shown on the page"
    assert any("postgres:18-alpine" in text for text in errors), errors

    # The status page polls every 2s — the message must survive the rerun.
    at.run()
    assert not at.exception, f"page crashed: {at.exception}"
    errors = [e.value for e in at.error]
    assert errors, "the failure message vanished on the next render"
    assert any("postgres:18-alpine" in text for text in errors), errors


# --- the service-level install axis -----------------------------------------


def test_service_install_is_the_primary_installable_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service with two installables installs the primary — and only it.

    llama-swap exposes the proxy (primary) and the llama-server binary.
    The one-click install must keep meaning "the primary".
    """
    worker = _make_worker(tmp_path)
    svc = worker.service("llama_swap")
    installables = svc.installs()
    assert len(installables) >= 2, "llama-swap is expected to expose several installables"

    primary = svc.primary_installable()
    assert primary is not None

    calls: list[str] = []

    def _install(self: Any, *, version: str | None = None) -> Any:
        calls.append(self.name)
        return _StubSession()

    monkeypatch.setattr(type(primary), "install", _install)
    monkeypatch.setattr(type(primary), "state", lambda self: _not_installed())

    session = svc.service_install()
    assert session is not None
    session.wait()

    assert calls == [primary.name]
    assert [i.name for i in svc.missing_installables()] == [primary.name]


def _not_installed():
    from genesis_worker.contracts import InstallState

    return InstallState.NOT_INSTALLED


def test_compose_service_install_covers_every_declared_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compose service's install covers every declared image, in order.

    A recorded version wins over the default tag: the operator's pick from
    the Images page is never clobbered by the one-click install.
    """
    worker = _make_worker(tmp_path)
    svc = worker.service("paperless")
    assert isinstance(svc, ComposeService)

    # The operator pinned the database to 16-alpine on the Images page.
    selection = (
        worker.settings.paths.state_dir / "paperless" / "install" / "paperless-db" / "current"
    )
    selection.parent.mkdir(parents=True, exist_ok=True)
    selection.write_text("16-alpine")

    calls = _patch_docker(monkeypatch, present=False)

    session = svc.service_install()
    assert session is not None
    session.wait()

    assert calls["install"] == [
        ("paperless:paperless", None),
        ("paperless:paperless-db", "16-alpine"),
        ("paperless:paperless-broker", None),
    ]


def test_multi_install_session_fails_with_the_image_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composite session's failure names the image and keeps the reason."""

    def _install(self: Any, *, version: str | None = None) -> Any:
        if self.name == "paperless:paperless-broker":
            return _StubSession(
                AcquireStateKind.FAILED,
                title="Failed: valkey/valkey:9-alpine",
                error="RuntimeError: docker pull valkey/valkey:9-alpine failed (rc=1): denied",
            )
        return _StubSession()

    monkeypatch.setattr(DockerImageInstall, "install", _install)
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: False))

    worker = _make_worker(tmp_path)
    svc = worker.service("paperless")
    assert isinstance(svc, ComposeService)

    session = svc.service_install()
    assert session is not None
    final = session.wait()

    assert final.kind == AcquireStateKind.FAILED
    assert "valkey/valkey:9-alpine" in (final.error or "")
    assert "denied" in (final.error or "")
    assert "valkey/valkey:9-alpine" in (final.title or "")


def test_start_after_a_clean_install_uses_the_pulled_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the install, start() renders the effective refs and brings the stack up."""
    worker = _make_worker(tmp_path)
    svc = worker.service("paperless")
    assert isinstance(svc, ComposeService)

    calls: list[str] = []
    monkeypatch.setattr(
        DockerComposeStack,
        "pull",
        lambda self: (calls.append("pull"), StartResult(ok=True, message="pulled"))[1],
    )
    monkeypatch.setattr(
        DockerComposeStack,
        "up",
        lambda self, *, env=None: (calls.append("up"), StartResult(ok=True, message="up"))[1],
    )
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: True))

    result = svc.start()
    assert result.ok, result.message
    assert calls == ["pull", "up"]

    compose_file = worker.settings.paths.state_dir / "paperless" / "compose" / "docker-compose.yml"
    body = compose_file.read_text()
    assert "image: ghcr.io/paperless-ngx/paperless-ngx:latest" in body
    assert "image: postgres:18-alpine" in body
    assert "image: valkey/valkey:9-alpine" in body


def test_compose_spec_with_an_undeclared_template_image_is_rejected(
    tmp_path: Path,
) -> None:
    """A template image nobody declared is rejected at load time.

    The availability check reads the template's images, the install axis is
    built from the declared ones — an undeclared image would leave the
    service uninstallable from the UI, forever and silently.
    """
    import yaml

    payload = {
        "version": 1,
        "kind": "compose",
        "name": "paperless",
        "display_name": "Paperless-ngx",
        "description": "demo",
        "category": "utility",
        "capabilities": {
            "can_generate_config": False,
            "can_export_for_agent": False,
            "can_serve_llm": False,
            "can_serve_image": False,
            "can_train_models": False,
            "has_web_ui": True,
            "can_install": True,
        },
        "resource_estimate": {
            "vram_bytes_typical": 0,
            "vram_bytes_min": 0,
            "cpu_cores_recommended": 1,
        },
        "compose_template": (
            'version: "3.8"\n'
            "services:\n"
            "  app:\n"
            "    image: $images.app\n"
            "  cache:\n"
            "    image: redis:7\n"
        ),
        "images": [{"name": "app", "repo": "example/app", "default_tag": "latest"}],
    }
    path = tmp_path / "paperless.yaml"
    path.write_text(yaml.safe_dump(payload))

    from genesis_worker.tests._factories import service_ctx

    with pytest.raises(ValueError, match="redis:7"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name="paperless"))
