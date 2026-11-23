"""Tests for ``utils.state.enabled_services`` — load/save round-trip + atomic write."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from genesis_worker.utils.state.enabled_services import (
    enabled_set_key,
    enabled_set_path,
    load_enabled_set,
    read_enabled_snapshot,
    save_enabled_set,
)


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_enabled_set(tmp_path) is None


def test_round_trip(tmp_path: Path) -> None:
    save_enabled_set(tmp_path, {"llama_swap", "crawl4ai", "comfyui"})
    assert load_enabled_set(tmp_path) == {"llama_swap", "crawl4ai", "comfyui"}


def test_empty_set_persists(tmp_path: Path) -> None:
    save_enabled_set(tmp_path, set())
    assert load_enabled_set(tmp_path) == set()


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    save_enabled_set(nested, {"foo"})
    assert (nested / "enabled_services.yaml").is_file()


def test_save_writes_atomically_no_tmp_left_behind(tmp_path: Path) -> None:
    save_enabled_set(tmp_path, {"foo"})
    # The atomic-write tmp file must not linger after the call.
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"tmp file leaked: {leftover}"


def test_save_overwrites_existing(tmp_path: Path) -> None:
    save_enabled_set(tmp_path, {"a", "b"})
    save_enabled_set(tmp_path, {"c"})
    assert load_enabled_set(tmp_path) == {"c"}


def test_load_ignores_malformed_payloads(tmp_path: Path) -> None:
    """Garbage in the file is treated as None — caller re-bootstraps."""
    (tmp_path / "enabled_services.yaml").write_text("not yaml at all: [")
    assert load_enabled_set(tmp_path) is None


def test_load_ignores_non_dict_payload(tmp_path: Path) -> None:
    (tmp_path / "enabled_services.yaml").write_text("- just\n- a\n- list\n")
    assert load_enabled_set(tmp_path) is None


def test_load_drops_non_string_entries(tmp_path: Path) -> None:
    """Type-tagged YAML that bypasses strict parsing shouldn't blow up the loader."""
    (tmp_path / "enabled_services.yaml").write_text("enabled:\n  - llama_swap\n  - 123\n  - null\n")
    assert load_enabled_set(tmp_path) == {"llama_swap"}


def test_save_uses_stable_sort(tmp_path: Path) -> None:
    """Stable output makes diffs against git pleasant."""
    save_enabled_set(tmp_path, {"z", "a", "m"})
    text = (tmp_path / "enabled_services.yaml").read_text()
    assert text.index("a") < text.index("m") < text.index("z")


# --- snapshots (ADR-054) -----------------------------------------------------


def test_snapshot_reports_the_file_identity(tmp_path: Path) -> None:
    """The snapshot carries the identity of the version it parsed."""
    save_enabled_set(tmp_path, {"llama_swap"})
    snapshot = read_enabled_snapshot(tmp_path)

    assert snapshot.readable is True
    assert snapshot.names == frozenset({"llama_swap"})
    assert snapshot.key == enabled_set_key(tmp_path)
    assert snapshot.key is not None


def test_snapshot_is_unreadable_for_a_missing_file(tmp_path: Path) -> None:
    snapshot = read_enabled_snapshot(tmp_path)
    assert snapshot.readable is False
    assert snapshot.key is None
    assert enabled_set_key(tmp_path) is None


def test_snapshot_is_unreadable_for_a_malformed_file(tmp_path: Path) -> None:
    (tmp_path / "enabled_services.yaml").write_text("enabled: [unclosed\n")
    snapshot = read_enabled_snapshot(tmp_path)
    assert snapshot.readable is False
    # The identity is still known — the file is there, its content is junk.
    assert snapshot.key == enabled_set_key(tmp_path)


def test_snapshot_reads_the_content_of_the_inode_it_fstat_ed(tmp_path: Path) -> None:
    """An open descriptor keeps reading the inode it opened, after a replace."""
    path = enabled_set_path(tmp_path)
    save_enabled_set(tmp_path, {"first"})
    with open(path, encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        save_enabled_set(tmp_path, {"second"})  # publishes a new inode
        text = fh.read()

    assert "first" in text
    assert (st.st_ino, st.st_mtime_ns, st.st_size) != enabled_set_key(tmp_path)


def test_save_uses_a_unique_tmp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two writers never share a tmp path (ADR-054)."""
    seen: list[Path] = []
    real_replace = os.replace

    def recording_replace(src, dst) -> None:
        seen.append(Path(src))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)
    save_enabled_set(tmp_path, {"a"})
    save_enabled_set(tmp_path, {"b"})

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all(p.name.endswith(".tmp") for p in seen)
