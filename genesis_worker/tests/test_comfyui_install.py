"""Tests for ``ComfyUiImage`` — version cache, install session, uninstall."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from genesis_worker.contracts import AcquireView
from genesis_worker.services.comfyui.install import (
    ComfyUiImage,
    _cache_path,
    _read_cache,
    _write_cache,
)
from genesis_worker.utils.install import _Canceled
from genesis_worker.utils.process import DockerContainer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_terminal(session) -> AcquireView:  # type: ignore[no-untyped-def]
    session.wait()
    return session.current_step()


def _make_installable(
    tmp_path: Path,
    *,
    image_repo: str = "ghcr.io/genesis-scaffolding/comfyui-cuda",
    image_tag: str = "v0.34.0-cuda-13.0-amd64",
) -> ComfyUiImage:
    return ComfyUiImage(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        image_repo=image_repo,
        image_tag=image_tag,
    )


# --- image_ref / source_url ------------------------------------------------


def test_image_ref_format(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    assert inst.image_ref == "ghcr.io/genesis-scaffolding/comfyui-cuda:v0.34.0-cuda-13.0-amd64"


def test_source_url_points_to_ghcr(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    url = inst.source_url()
    assert url is not None
    assert "comfyui-cuda" in url


def test_binary_path_is_none(tmp_path: Path) -> None:
    """There is no host binary; the service overrides ``is_available``."""
    inst = _make_installable(tmp_path)
    assert inst.binary_path() is None


# --- state / installed_version --------------------------------------------


def test_state_installed_when_image_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    assert _make_installable(tmp_path).state().value == "installed"


def test_state_not_installed_when_image_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: False))
    assert _make_installable(tmp_path).state().value == "not_installed"


def test_installed_version_reads_selection_file(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("v0.34.0-cuda-13.0-amd64\n")
    assert inst.installed_version() == "v0.34.0-cuda-13.0-amd64"


def test_installed_version_none_when_no_selection(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    assert inst.installed_version() is None


# --- available_versions + cache -------------------------------------------


def test_available_versions_uses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a fresh cache exists, ``list_remote_tags`` is not called."""
    cache = _cache_path(tmp_path / "cache", "ghcr.io/genesis-scaffolding/comfyui-cuda")
    _write_cache(cache, ["v0.34.0-cuda-13.0-amd64", "v0.33.0-cuda-12.8-amd64"])

    called = {"count": 0}

    def _fake_list(repo):  # type: ignore[no-untyped-def]
        called["count"] += 1
        return []

    monkeypatch.setattr(DockerContainer, "list_remote_tags", staticmethod(_fake_list))

    versions = _make_installable(tmp_path).available_versions()
    assert [v.version for v in versions] == [
        "v0.34.0-cuda-13.0-amd64",
        "v0.33.0-cuda-12.8-amd64",
    ]
    assert called["count"] == 0


def test_available_versions_fetches_when_cache_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache_path(tmp_path / "cache", "ghcr.io/genesis-scaffolding/comfyui-cuda")
    payload = {"version": 1, "fetched_at": time.time() - 3600, "tags": ["old-tag"]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("w") as f:
        json.dump(payload, f)

    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo: ["v0.34.0-cuda-13.0-amd64"]),
    )
    versions = _make_installable(tmp_path).available_versions()
    assert [v.version for v in versions] == ["v0.34.0-cuda-13.0-amd64"]


def test_available_versions_writes_cache_on_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo: ["v0.34.0-cuda-13.0-amd64"]),
    )
    _make_installable(tmp_path).available_versions()
    cache = _cache_path(tmp_path / "cache", "ghcr.io/genesis-scaffolding/comfyui-cuda")
    assert cache.is_file()
    assert _read_cache(cache, ttl_s=3600) == ["v0.34.0-cuda-13.0-amd64"]


def test_invalidate_versions_cache_removes_file(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    cache = _cache_path(tmp_path / "cache", "ghcr.io/genesis-scaffolding/comfyui-cuda")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}")
    inst.invalidate_versions_cache()
    assert not cache.exists()


def test_available_versions_returns_install_version_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo: ["v1"]),
    )
    versions = _make_installable(tmp_path).available_versions()
    assert len(versions) == 1
    assert versions[0].version == "v1"
    assert versions[0].url == "ghcr.io/genesis-scaffolding/comfyui-cuda:v1"
    assert versions[0].sha256 is None


# --- install session ------------------------------------------------------


def test_install_session_runs_docker_pull(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        captured.append([image])
        if progress is not None:
            progress("Pulling fs layer")
            progress("Pull complete")

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="v0.99.0-cuda-13.0-amd64")
    step = _wait_for_terminal(session)
    assert step.kind == "complete"
    assert "pulled" in (step.title or "")
    assert captured == [["ghcr.io/genesis-scaffolding/comfyui-cuda:v0.99.0-cuda-13.0-amd64"]]


def test_install_session_publishes_progress_from_json_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``docker pull`` JSON lines should populate ``step.progress`` so the UI bar fills."""
    progress_lines = [
        '{"status":"Pulling fs layer","id":"abc"}',
        '{"status":"Downloading","progressDetail":{"current":1000,"total":5000},"id":"abc"}',
        '{"status":"Downloading","progressDetail":{"current":4000,"total":5000},"id":"abc"}',
        '{"status":"Extracting","progressDetail":{"current":4000,"total":5000},"id":"abc"}',
        '{"status":"Pull complete","id":"abc"}',
    ]

    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        for line in progress_lines:
            if progress is not None:
                progress(line)

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="v1")
    # Drain the worker; final step is "complete", but we want to assert
    # the in-flight step had progress populated.
    # Use a deadline-based poll on current_step to read an intermediate state.
    import time
    deadline = time.monotonic() + 2.0
    seen_with_progress = None
    while time.monotonic() < deadline:
        step = session.current_step()
        if step.kind == "fetching" and step.progress is not None and step.progress.bytes_total > 0:
            seen_with_progress = step
            break
        if step.kind in ("complete", "failed", "cancelled"):
            break
        time.sleep(0.01)
    # Cancel to let the worker exit cleanly.
    session.cancel()
    session.wait()

    assert seen_with_progress is not None, "expected at least one fetching step with progress populated"
    assert seen_with_progress.progress is not None
    assert seen_with_progress.progress.bytes_total == 5000
    assert 0 < seen_with_progress.progress.bytes_done <= 5000


def test_install_session_uses_json_progress_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the install session requests ``--progress=json`` from docker."""
    seen_format: list[str] = []

    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        seen_format.append(progress_format)

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))
    inst = _make_installable(tmp_path)
    session = inst.install(version="v1")
    _wait_for_terminal(session)
    assert seen_format == ["json"]


def test_install_session_records_selection_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        pass

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="v0.99.0-cuda-13.0-amd64")
    _wait_for_terminal(session)
    assert inst.installed_version() == "v0.99.0-cuda-13.0-amd64"


def test_install_session_failure_surfaces_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        raise RuntimeError("manifest unknown")

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="v0.99.0-cuda-13.0-amd64")
    step = _wait_for_terminal(session)
    assert step.kind == "failed"
    assert "manifest unknown" in (step.error or "")


def test_install_session_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel between progress lines produces a 'cancelled' final step.

    The fake pull sleeps between iterations to simulate docker pull
    latency; the test cancels mid-flight.
    """

    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        if progress is None or cancel is None:
            return
        for i in range(100):
            progress(f"layer {i}")
            if cancel():
                raise _Canceled
            time.sleep(0.02)  # simulate docker pull latency per layer

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="v1")
    # Give the worker time to start iterating.
    time.sleep(0.05)
    session.cancel()
    step = _wait_for_terminal(session)
    assert step.kind == "cancelled", f"expected cancelled, got {step.kind}"


# --- uninstall ------------------------------------------------------------


def test_uninstall_runs_docker_rmi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    inst = _make_installable(tmp_path)
    inst.uninstall(version="v0.34.0-cuda-13.0-amd64")
    assert captured
    assert captured[0][:2] == ["docker", "rmi"]
    assert captured[0][2] == "ghcr.io/genesis-scaffolding/comfyui-cuda:v0.34.0-cuda-13.0-amd64"


def test_uninstall_clears_selection_when_matching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr=""),
    )
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("v0.34.0-cuda-13.0-amd64")
    inst.uninstall(version="v0.34.0-cuda-13.0-amd64")
    assert not inst._selection_path.exists()


def test_uninstall_keeps_selection_when_different_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr=""),
    )
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("v0.34.0-cuda-13.0-amd64")
    inst.uninstall(version="v0.99.0-cuda-13.0-amd64")
    assert inst._selection_path.read_text() == "v0.34.0-cuda-13.0-amd64"


def test_uninstall_noop_when_no_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        called.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    _make_installable(tmp_path).uninstall()
    assert called == []  # No docker rmi when nothing to uninstall.
