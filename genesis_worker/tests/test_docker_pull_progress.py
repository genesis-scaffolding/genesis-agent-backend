"""Tests for the ``DockerPullProgress`` JSON-line parser."""

from __future__ import annotations

import pytest

from genesis_worker.utils.process.docker_pull_progress import DockerPullProgress


def test_empty_snapshot_returns_zero() -> None:
    snap = DockerPullProgress().snapshot()
    assert snap.bytes_done == 0
    assert snap.bytes_total == 0
    assert snap.phase == "Working"


def test_summarises_two_layer_download() -> None:
    """bytes_total sums each layer's total; bytes_done sums each layer's current."""
    p = DockerPullProgress()
    p.update('{"status":"Pulling fs layer","id":"abc"}')
    p.update('{"status":"Pulling fs layer","id":"def"}')
    p.update('{"status":"Downloading","progressDetail":{"current":1000,"total":5000},"id":"abc"}')
    p.update('{"status":"Downloading","progressDetail":{"current":2000,"total":4000},"id":"def"}')
    snap = p.snapshot()
    assert snap.bytes_total == 9000
    assert snap.bytes_done == 3000
    assert snap.phase == "Downloading"


def test_ignores_non_json_lines() -> None:
    p = DockerPullProgress()
    p.update("not json at all")
    p.update("")
    p.update("   ")
    snap = p.snapshot()
    assert snap.bytes_done == 0
    assert snap.bytes_total == 0


def test_ignores_non_dict_json() -> None:
    p = DockerPullProgress()
    p.update("[1, 2, 3]")
    p.update('"a string"')
    p.update("42")
    snap = p.snapshot()
    assert snap.bytes_total == 0


def test_phase_transitions() -> None:
    p = DockerPullProgress()
    p.update('{"status":"Pulling fs layer","id":"abc"}')
    assert p.snapshot().phase == "Pulling fs layer"
    p.update('{"status":"Downloading","progressDetail":{"current":1,"total":2},"id":"abc"}')
    assert p.snapshot().phase == "Downloading"
    p.update('{"status":"Verifying Checksum","id":"abc"}')
    assert p.snapshot().phase == "Verifying checksum"
    p.update('{"status":"Extracting","progressDetail":{"current":1,"total":2},"id":"abc"}')
    assert p.snapshot().phase == "Extracting"
    p.update('{"status":"Pull complete","id":"abc"}')
    assert p.snapshot().phase == "Pull complete"


def test_pull_level_status_event_marks_complete() -> None:
    """A 'Status: Downloaded' or 'Status: Image is up to date' sets COMPLETE."""
    p = DockerPullProgress()
    p.update('{"status":"Status: Downloaded newer image for img:latest"}')
    assert p.snapshot().phase == "Pull complete"


def test_clamps_bytes_done_to_total_per_layer() -> None:
    """If a layer reports current > total, we clamp to total in the sum."""
    p = DockerPullProgress()
    p.update('{"status":"Downloading","progressDetail":{"current":10000,"total":5000},"id":"abc"}')
    snap = p.snapshot()
    assert snap.bytes_done == 5000  # clamped
    assert snap.bytes_total == 5000


def test_layer_with_no_total_contributes_zero_to_total_only() -> None:
    """A layer seen with no total is still tracked, contributes 0 to total until total arrives."""
    p = DockerPullProgress()
    p.update('{"status":"Downloading","id":"abc"}')  # no progressDetail
    snap = p.snapshot()
    assert snap.bytes_total == 0
    assert snap.bytes_done == 0
    p.update('{"status":"Downloading","progressDetail":{"current":100,"total":500},"id":"abc"}')
    snap = p.snapshot()
    assert snap.bytes_total == 500
    assert snap.bytes_done == 100


# --- plain-text fallback (Docker < 23.0) ----------------------------------


def test_plain_text_pulling_fs_layer_sets_phase() -> None:
    p = DockerPullProgress()
    p.update("v0.34.0-cuda-13.0-amd64: Pulling fs layer")
    assert p.snapshot().phase == "Pulling fs layer"


def test_plain_text_downloading_sets_phase() -> None:
    p = DockerPullProgress()
    p.update("v0.34.0-cuda-13.0-amd64: Downloading  100MB / 200MB")
    assert p.snapshot().phase == "Downloading"


def test_plain_text_pull_complete_sets_phase() -> None:
    p = DockerPullProgress()
    p.update("v0.34.0-cuda-13.0-amd64: Pull complete")
    assert p.snapshot().phase == "Pull complete"


def test_plain_text_does_not_populate_byte_counts() -> None:
    """Plain-text output carries no per-layer bytes — totals stay zero."""
    p = DockerPullProgress()
    p.update("v0.34.0-cuda-13.0-amd64: Downloading  100MB / 200MB")
    snap = p.snapshot()
    assert snap.bytes_total == 0
    assert snap.bytes_done == 0


def test_plain_text_status_line_marks_complete() -> None:
    p = DockerPullProgress()
    p.update("Status: Downloaded newer image for img:latest")
    assert p.snapshot().phase == "Pull complete"


# --- supports_json_probes via docker pull --help -----------------------


def test_supports_json_progress_probes_docker_pull_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probes ``docker pull --help`` for the ``--progress`` flag, caches result."""
    import subprocess as _subprocess

    from genesis_worker.utils.process import docker as docker_mod
    from genesis_worker.utils.process.docker import DockerContainer

    cases = [
        (
            "  -a, --all-tags          ...\n      --platform string   ...\n      --progress string   ...\n  -q, --quiet",
            True,
        ),
        ("  -a, --all-tags\n      --platform string\n  -q, --quiet", False),
    ]
    for help_text, expected in cases:
        docker_mod._PROGRESS_SUPPORT_CACHE.clear()

        def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
            assert args == ["docker", "pull", "--help"]
            return _subprocess.CompletedProcess(
                args=args, returncode=0, stdout=help_text, stderr=""
            )

        monkeypatch.setattr(docker_mod, "_run", _fake_run)
        assert DockerContainer.supports_json_progress() is expected

    # Subsequent calls should not re-invoke docker.
    calls: list[list[str]] = []

    def _counting_run(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _subprocess.CompletedProcess(
            args=args, returncode=0, stdout="  --progress string   ...\n", stderr=""
        )

    monkeypatch.setattr(docker_mod, "_run", _counting_run)
    DockerContainer.supports_json_progress()
    DockerContainer.supports_json_progress()
    assert len(calls) == 0  # cached after the first call in this scope


def test_supports_json_progress_false_when_docker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``docker`` not on PATH → report unsupported, don't crash."""
    from genesis_worker.utils.process import docker as docker_mod
    from genesis_worker.utils.process.docker import DockerContainer

    docker_mod._PROGRESS_SUPPORT_CACHE.clear()
    monkeypatch.setattr(
        docker_mod, "_run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError)
    )
    assert DockerContainer.supports_json_progress() is False
