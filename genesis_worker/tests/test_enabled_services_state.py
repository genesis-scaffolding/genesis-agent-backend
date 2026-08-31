"""Tests for ``utils.state.enabled_services`` — load/save round-trip + atomic write."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.utils.state.enabled_services import (
    load_enabled_set,
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
