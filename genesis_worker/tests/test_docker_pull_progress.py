"""Tests for the ``DockerPullProgress`` JSON-line parser."""

from __future__ import annotations

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
