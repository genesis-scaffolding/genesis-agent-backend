"""AppTest for the ComfyUI Image page.

The page has no behavioural coverage today: ``test_comfyui_ui_imports.py``
only AST-parses it. These tests drive the real page through ``AppTest``
with the registry and the docker daemon stubbed out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from genesis_worker import GenesisWorker
from genesis_worker.contracts import AcquireSession, AcquireStateKind, AcquireView
from genesis_worker.services.comfyui.install import ComfyUiImage
from genesis_worker.services.comfyui.service import ComfyUiService
from genesis_worker.settings import PathsSettings, Settings

_PAGE = Path(__file__).resolve().parents[1] / "services" / "comfyui" / "ui" / "image.py"


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


class _DoneSession:
    """A finished install session."""

    def view(self) -> AcquireView:
        return AcquireView(kind=AcquireStateKind.COMPLETE, title="done")

    def cancel(self) -> None:
        pass


def _patch_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the docker daemon and the GPU snapshot the page reads."""
    from genesis_worker.services.comfyui.service import ComfyUiService
    from genesis_worker.utils.process import DockerContainer

    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda ref: False))
    monkeypatch.setattr(ComfyUiService, "has_amd_gpu", property(lambda self: True))
    monkeypatch.setattr(ComfyUiService, "has_nvidia_gpu", property(lambda self: True))
    monkeypatch.setattr(ComfyUiService, "is_running", lambda self: False)


def _patch_versions(monkeypatch: pytest.MonkeyPatch, tags: list[str]) -> None:
    def _versions(self: Any) -> list[Any]:
        from genesis_worker.contracts import InstallVersion

        return [
            InstallVersion(
                version=t,
                url=f"{self.image_ref.rsplit(':', 1)[0]}:{t}",
                sha256=None,
                size_bytes=None,
            )
            for t in tags
        ]

    monkeypatch.setattr(ComfyUiImage, "available_versions", _versions)


def _run(worker: GenesisWorker) -> AppTest:
    at = AppTest.from_file(str(_PAGE), default_timeout=30)
    at.session_state["worker"] = worker
    return at


def test_empty_tag_list_still_offers_install_and_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_host(monkeypatch)
    _patch_versions(monkeypatch, [])
    worker = _make_worker(tmp_path)

    at = _run(worker)
    at.run()

    assert any("No tags returned from the registry" in c.value for c in at.caption)
    labels = [b.label for b in at.button]
    assert "Install" in labels
    assert "Refresh tags" in labels


def test_tag_picker_offers_the_registry_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_host(monkeypatch)
    _patch_versions(monkeypatch, ["v0.43.0-rocm-7.14-amd64", "latest-rocm-7.14-amd64"])
    worker = _make_worker(tmp_path)

    at = _run(worker)
    at.run()

    options = [o for s in at.selectbox for o in s.options]
    assert "latest-rocm-7.14-amd64" in options


def test_install_persists_the_chosen_tag_as_the_service_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_host(monkeypatch)
    _patch_versions(monkeypatch, ["v0.43.0-rocm-7.14-amd64"])
    installs: list[tuple[str, str | None]] = []

    def _install(self: Any, *, version: str | None = None) -> AcquireSession:
        installs.append((self.name, version))
        return _DoneSession()  # type: ignore[return-value]

    monkeypatch.setattr(ComfyUiImage, "install", _install)
    worker = _make_worker(tmp_path)

    at = _run(worker)
    at.run()
    at.button(key="install-comfyui-rocm").click().run()

    assert ("comfyui-rocm", "v0.43.0-rocm-7.14-amd64") in installs


def test_completed_install_writes_the_tag_to_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_host(monkeypatch)
    _patch_versions(monkeypatch, ["v0.43.0-rocm-7.14-amd64"])

    class _CompleteSession:
        def view(self) -> AcquireView:
            return AcquireView(kind=AcquireStateKind.COMPLETE, title="done")

        def cancel(self) -> None:
            pass

    def _install(self: Any, *, version: str | None = None) -> AcquireSession:
        return _CompleteSession()  # type: ignore[return-value]

    monkeypatch.setattr(ComfyUiImage, "install", _install)
    worker = _make_worker(tmp_path)

    at = _run(worker)
    at.run()
    at.button(key="install-comfyui-rocm").click().run()

    overrides = worker.read_service_overrides("comfyui")
    assert overrides.get("rocm_image_tag") == "v0.43.0-rocm-7.14-amd64"
    svc = worker.service("comfyui")
    assert isinstance(svc, ComfyUiService)
    assert svc.image_tag("rocm") == "v0.43.0-rocm-7.14-amd64"
