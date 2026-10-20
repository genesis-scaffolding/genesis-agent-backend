"""Tests for the shared Images page for declarative docker / compose services.

Covers three layers:

- pure logic in ``utils.ui.default_images`` (effective ref, rows) —
  fake installables, no Streamlit;
- the framework ``ui_pages`` default (docker / compose get the Images
  page, uv services don't);
- the Streamlit renderer itself through ``AppTest`` with docker /
  registry state monkeypatched away (hermetic).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from genesis_worker import GenesisWorker
from genesis_worker.contracts import (
    AcquireSession,
    AcquireStateKind,
    AcquireView,
    InstallState,
    InstallVersion,
    ServiceInstall,
)
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.utils.process import DockerContainer
from genesis_worker.utils.services import DockerImageInstall
from genesis_worker.utils.ui.default_images import (
    effective_ref,
    image_rows,
    ref_present,
)

# --- pure logic -------------------------------------------------------------


class _FakeInstall(ServiceInstall):
    """Minimal ServiceInstall for pure-logic tests (no acquire session)."""

    def __init__(
        self,
        *,
        name: str = "svc:app",
        image_ref: str | None = "repo/img:1.0",
        version: str | None = None,
        source_url: str | None = None,
    ) -> None:
        self.name = name
        self._image_ref = image_ref
        self._version = version
        self._source_url = source_url

    @property
    def image_ref(self) -> str | None:
        return self._image_ref

    def state(self) -> InstallState:
        return InstallState.INSTALLED

    def installed_version(self) -> str | None:
        return self._version

    def available_versions(self) -> list[InstallVersion]:
        return []

    def binary_path(self) -> Path | None:
        return None

    def install(self, *, version: str | None = None) -> AcquireSession:
        raise NotImplementedError

    def uninstall(self, *, version: str | None = None) -> None:
        return None

    def source_url(self) -> str | None:
        return self._source_url


def test_effective_ref_uses_default_when_no_selection() -> None:
    inst = _FakeInstall()
    assert effective_ref(inst) == "repo/img:1.0"


def test_effective_ref_uses_recorded_selection_over_default() -> None:
    inst = _FakeInstall(version="2.3.4")
    assert effective_ref(inst) == "repo/img:2.3.4"


def test_effective_ref_none_for_non_docker_installable() -> None:
    inst = _FakeInstall(image_ref=None)
    assert effective_ref(inst) is None


def test_image_rows_maps_all_installables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: True))
    insts = [_FakeInstall(name="a"), _FakeInstall(name="b", version="9")]

    class _Svc:
        name = "svc"

        def installs(self) -> list:
            return insts

    rows = image_rows(_Svc())  # type: ignore[arg-type]
    assert [r.installable.name for r in rows] == ["a", "b"]
    assert rows[0].ref == "repo/img:1.0"
    assert rows[1].ref == "repo/img:9"
    assert rows[1].version == "9"
    assert all(r.present for r in rows)


def test_ref_present_false_when_image_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: False))
    assert ref_present(_FakeInstall()) is False


def test_ref_present_true_when_image_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: True))
    assert ref_present(_FakeInstall()) is True


# --- ui_pages ---------------------------------------------------------------


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


def test_docker_declarative_service_ships_images_page(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    svc = worker.service("bifrost")
    pages = svc.ui_pages
    labels = [p.label for p in pages]
    assert labels[:2] == ["Status", "Images"]
    image_page = pages[1]
    assert image_page.url_path == "bifrost_images"
    assert image_page.path.name == "default_service_images.py"
    assert image_page.path.is_file()


def test_compose_declarative_service_ships_images_page(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    svc = worker.service("romm")
    pages = svc.ui_pages
    assert [p.label for p in pages][:2] == ["Status", "Images"]
    assert pages[1].url_path == "romm_images"
    assert pages[1].path.is_file()


def test_uv_service_does_not_ship_images_page(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    svc = worker.service("cptr")
    labels = [p.label for p in svc.ui_pages]
    assert labels == ["Status"]


# --- Streamlit rendering ----------------------------------------------------


def _render(worker: GenesisWorker, name: str) -> AppTest:
    body = textwrap.dedent(
        f"""
        import streamlit as st
        from genesis_worker.utils.ui.default_images import render_default_images

        worker = st.session_state["worker"]
        svc = worker.service("{name}")
        render_default_images(svc)
        """
    )
    driver = worker._settings.paths.log_dir.parent / "_images_driver.py"
    driver.parent.mkdir(parents=True, exist_ok=True)
    driver.write_text(body)
    at = AppTest.from_file(str(driver), default_timeout=30)
    at.session_state["worker"] = worker
    return at


def _patch_install_axis(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Hermetic registry / image state. Returns a call log."""
    calls: dict[str, Any] = {"install": [], "uninstall": []}

    def _install(self: Any, *, version: str | None = None) -> Any:
        calls["install"].append((self.name, version))
        return _FinishedSession()

    def _versions(self: Any) -> list[InstallVersion]:
        return [
            InstallVersion(
                version=t,
                url=f"{self.image_ref.rsplit(':', 1)[0]}:{t}",
                sha256=None,
                size_bytes=None,
            )
            for t in ("2.0.0", "1.0.0")
        ]

    monkeypatch.setattr(DockerImageInstall, "install", _install)
    monkeypatch.setattr(DockerImageInstall, "available_versions", _versions)
    monkeypatch.setattr(DockerImageInstall, "installed_version", lambda self: None)
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: False))
    return calls


class _FinishedSession:
    """Acquire session whose view is permanently complete (no polling)."""

    def view(self) -> AcquireView:
        return AcquireView(kind=AcquireStateKind.COMPLETE, title="done")

    def cancel(self) -> None:
        pass


def test_images_page_renders_all_compose_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path)
    _patch_install_axis(monkeypatch)
    at = _render(worker, "romm")
    at.run()
    assert not at.exception, f"page crashed: {at.exception}"

    expander_labels = [e.label for e in at.expander]
    assert "romm:romm" in expander_labels
    assert "romm:romm-db" in expander_labels
    # One tag picker per image.
    assert len(at.selectbox) == 2
    # No version recorded + service stopped -> Uninstall disabled, Install live.
    for b in at.button:
        key = b.key or ""
        if key.startswith("uninstall-"):
            assert b.disabled
        if key.startswith("install-"):
            assert not b.disabled


def test_images_page_button_says_update_when_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path)
    _patch_install_axis(monkeypatch)
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: True))
    at = _render(worker, "bifrost")
    at.run()
    assert not at.exception
    install_buttons = [b for b in at.button if (b.key or "").startswith("install-")]
    assert len(install_buttons) == 1
    assert install_buttons[0].label == "Update"


def test_install_click_pulls_selected_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _make_worker(tmp_path)
    calls = _patch_install_axis(monkeypatch)
    at = _render(worker, "bifrost")
    at.run()
    assert not at.exception

    at.selectbox(key="tag-bifrost-bifrost").select("1.0.0")
    at.button(key="install-bifrost-bifrost").click()
    at.run()
    assert calls["install"] == [("bifrost", "1.0.0")]


def test_install_defaults_to_default_tag_when_no_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path)
    calls = _patch_install_axis(monkeypatch)
    at = _render(worker, "bifrost")
    at.run()
    assert not at.exception

    at.button(key="install-bifrost-bifrost").click()
    at.run()
    # No selectbox interaction: the first (newest) tag is the default pick.
    assert calls["install"] == [("bifrost", "2.0.0")]


def test_uninstall_refused_while_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _make_worker(tmp_path)
    _patch_install_axis(monkeypatch)
    svc = worker.service("bifrost")
    monkeypatch.setattr(svc, "is_running", lambda: True)
    at = _render(worker, "bifrost")
    at.run()
    assert not at.exception
    uninstall = [b for b in at.button if (b.key or "").startswith("uninstall-")]
    assert uninstall and uninstall[0].disabled


def _patch_unreachable_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch that simulates registry returning no tags (unreachable / rate-limited)."""
    calls: dict[str, Any] = {"install": [], "uninstall": []}

    def _install(self: Any, *, version: str | None = None) -> Any:
        calls["install"].append((self.name, version))
        return _FinishedSession()

    monkeypatch.setattr(DockerImageInstall, "install", _install)
    monkeypatch.setattr(DockerImageInstall, "available_versions", lambda self: [])
    monkeypatch.setattr(DockerImageInstall, "installed_version", lambda self: "1.0.0")
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: False))
    return calls


def test_registry_unreachable_still_shows_install_at_resolved_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path)
    calls = _patch_unreachable_registry(monkeypatch)
    at = _render(worker, "bifrost")
    at.run()
    assert not at.exception
    # No selectbox since registry had no tags.
    assert len(at.selectbox) == 0
    # Install button must still be present (not hidden behind empty tag list).
    install_btn = [b for b in at.button if (b.key or "").startswith("install-")]
    assert len(install_btn) == 1
    assert install_btn[0].label == "Install"
    assert not install_btn[0].disabled
    # Uninstall is enabled because row.version is set (recorded selection);
    # the recorded file is removed on uninstall, not the local image.
    uninstall_btn = [b for b in at.button if (b.key or "").startswith("uninstall-")]
    assert uninstall_btn and not uninstall_btn[0].disabled
    # Clicking Install passes the recorded version as the tag.
    install_btn[0].click()
    at.run()
    assert calls["install"] == [("bifrost", "1.0.0")]


def test_registry_unreachable_update_button_when_image_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _make_worker(tmp_path)
    _patch_unreachable_registry(monkeypatch)
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: True))
    at = _render(worker, "bifrost")
    at.run()
    assert not at.exception
    install_btn = [b for b in at.button if (b.key or "").startswith("install-")]
    assert install_btn and install_btn[0].label == "Update"


def test_registry_unreachable_install_at_default_tag_when_no_recorded_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When available_versions is empty and no version is recorded, Install
    still works — it falls back to the image's default tag (e.g. 'latest').
    Uninstall is correctly disabled because row.version is None.
    """
    worker = _make_worker(tmp_path)
    calls: dict[str, Any] = {"install": []}

    def _install(self: Any, *, version: str | None = None) -> Any:
        calls["install"].append((self.name, version))
        return _FinishedSession()

    monkeypatch.setattr(DockerImageInstall, "install", _install)
    monkeypatch.setattr(DockerImageInstall, "available_versions", lambda self: [])
    monkeypatch.setattr(DockerImageInstall, "installed_version", lambda self: None)
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: False))
    at = _render(worker, "bifrost")
    at.run()
    assert not at.exception
    # No selectbox.
    assert len(at.selectbox) == 0
    # Install button still present (falls back to default tag 'latest' from image_ref).
    install_btn = [b for b in at.button if (b.key or "").startswith("install-")]
    assert len(install_btn) == 1
    assert install_btn[0].label == "Install"
    # Uninstall disabled: no version recorded.
    uninstall_btn = [b for b in at.button if (b.key or "").startswith("uninstall-")]
    assert uninstall_btn and uninstall_btn[0].disabled
    # Clicking install falls back to the default tag from image_ref.
    install_btn[0].click()
    at.run()
    assert calls["install"] == [("bifrost", "latest")]
