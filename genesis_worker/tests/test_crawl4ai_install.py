"""Tests for ``Crawl4AiImage`` — version cache, install session, uninstall."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from genesis_worker.contracts import AcquireView
from genesis_worker.services.crawl4ai.install import (
    Crawl4AiImage,
    _cache_path,
    _read_cache,
    _write_cache,
)
from genesis_worker.utils.background_session import _Canceled
from genesis_worker.utils.process import DockerContainer


def _wait_for_terminal(session) -> AcquireView:  # type: ignore[no-untyped-def]
    session.wait()
    return session.view()


def _make_installable(
    tmp_path: Path,
    *,
    image_repo: str = "unclecode/crawl4ai",
    image_tag: str = "latest",
) -> Crawl4AiImage:
    return Crawl4AiImage(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        image_repo=image_repo,
        image_tag=image_tag,
    )


# --- image_ref / source_url ------------------------------------------------


def test_image_ref_format(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    assert inst.image_ref == "unclecode/crawl4ai:latest"


def test_source_url_points_to_docker_hub(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    url = inst.source_url()
    assert url is not None
    assert "unclecode/crawl4ai" in url
    assert "hub.docker.com" in url


def test_binary_path_is_none(tmp_path: Path) -> None:
    """There is no host binary; the service overrides ``is_available``."""
    inst = _make_installable(tmp_path)
    assert inst.binary_path() is None


# --- state / installed_version --------------------------------------------


def test_state_installed_when_image_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    assert _make_installable(tmp_path).state().value == "installed"


def test_state_not_installed_when_image_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: False))
    assert _make_installable(tmp_path).state().value == "not_installed"


def test_installed_version_reads_selection_file(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("0.7.0\n")
    assert inst.installed_version() == "0.7.0"


def test_installed_version_none_when_no_selection(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    assert inst.installed_version() is None


# --- available_versions + cache -------------------------------------------


def test_available_versions_uses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a fresh cache exists, ``list_remote_tags`` is not called."""
    cache = _cache_path(tmp_path / "cache", "unclecode/crawl4ai")
    _write_cache(cache, ["latest", "0.7.0"])

    called = {"count": 0}

    def _fake_list(repo):  # type: ignore[no-untyped-def]
        called["count"] += 1
        return []

    monkeypatch.setattr(DockerContainer, "list_remote_tags", staticmethod(_fake_list))

    versions = _make_installable(tmp_path).available_versions()
    assert [v.version for v in versions] == ["latest", "0.7.0"]
    assert called["count"] == 0


def test_available_versions_fetches_when_cache_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = _cache_path(tmp_path / "cache", "unclecode/crawl4ai")
    payload = {"version": 1, "fetched_at": time.time() - 3600, "tags": ["old-tag"]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("w") as f:
        json.dump(payload, f)

    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo: ["latest", "0.7.0"]),
    )
    versions = _make_installable(tmp_path).available_versions()
    assert [v.version for v in versions] == ["latest", "0.7.0"]


def test_available_versions_writes_cache_on_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo: ["latest", "0.7.0"]),
    )
    _make_installable(tmp_path).available_versions()
    cache = _cache_path(tmp_path / "cache", "unclecode/crawl4ai")
    assert cache.is_file()
    assert _read_cache(cache, ttl_s=3600) == ["latest", "0.7.0"]


def test_invalidate_versions_cache_removes_file(tmp_path: Path) -> None:
    inst = _make_installable(tmp_path)
    cache = _cache_path(tmp_path / "cache", "unclecode/crawl4ai")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}")
    inst.invalidate_versions_cache()
    assert not cache.exists()


def test_available_versions_returns_install_version_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo: ["latest"]),
    )
    versions = _make_installable(tmp_path).available_versions()
    assert len(versions) == 1
    assert versions[0].version == "latest"
    assert versions[0].url == "unclecode/crawl4ai:latest"
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
    session = inst.install(version="0.7.0")
    step = _wait_for_terminal(session)
    assert step.kind == "complete"
    assert "Pulled" in (step.title or "")
    assert captured == [["unclecode/crawl4ai:0.7.0"]]


def test_install_session_records_selection_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        pass

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="0.7.0")
    _wait_for_terminal(session)
    assert inst.installed_version() == "0.7.0"


def test_install_session_failure_surfaces_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        raise RuntimeError("manifest unknown")

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="0.7.0")
    step = _wait_for_terminal(session)
    assert step.kind == "failed"
    assert "manifest unknown" in (step.error or "")


def test_install_session_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel between progress lines produces a 'cancelled' final step."""

    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        if progress is None or cancel is None:
            return
        for i in range(100):
            progress(f"layer {i}")
            if cancel():
                raise _Canceled
            time.sleep(0.02)

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))

    inst = _make_installable(tmp_path)
    session = inst.install(version="latest")
    time.sleep(0.05)
    session.cancel()
    step = _wait_for_terminal(session)
    assert step.kind == "cancelled", f"expected cancelled, got {step.kind}"


def test_install_session_uses_json_progress_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the install session requests ``--progress=json`` from docker."""
    seen_format: list[str] = []

    def _fake_pull(image, *, progress=None, cancel=None, timeout_s=1800.0, progress_format="json"):  # type: ignore[no-untyped-def]
        seen_format.append(progress_format)

    monkeypatch.setattr(DockerContainer, "pull", staticmethod(_fake_pull))
    inst = _make_installable(tmp_path)
    session = inst.install(version="latest")
    _wait_for_terminal(session)
    assert seen_format == ["json"]


# --- uninstall ------------------------------------------------------------


def test_uninstall_runs_docker_rmi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    inst = _make_installable(tmp_path)
    inst.uninstall(version="0.7.0")
    assert captured
    assert captured[0][:2] == ["docker", "rmi"]
    assert captured[0][2] == "unclecode/crawl4ai:0.7.0"


def test_uninstall_clears_selection_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("0.7.0")
    inst.uninstall(version="0.7.0")
    assert not inst._selection_path.exists()


def test_uninstall_keeps_selection_when_different_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    inst = _make_installable(tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    inst._selection_path.write_text("0.7.0")
    inst.uninstall(version="0.99.0")
    assert inst._selection_path.read_text() == "0.7.0"


def test_uninstall_noop_when_no_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        called.append(list(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    _make_installable(tmp_path).uninstall()
    assert called == []  # No docker rmi when nothing to uninstall.
