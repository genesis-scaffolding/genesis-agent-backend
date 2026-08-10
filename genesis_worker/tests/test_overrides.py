"""Tests for the OverridesStore."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.services.llama_swap.overrides import OverridesStore


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = OverridesStore(tmp_path / "overrides.yaml")
    assert store.load() == {}


def test_round_trip(tmp_path: Path) -> None:
    store = OverridesStore(tmp_path / "overrides.yaml")
    data = {
        "some-entry": {"sampling": {"temp": 0.6}, "reasoning_budget": 8192},
    }
    store.save(data)
    assert store.load() == data


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "dir" / "overrides.yaml"
    store = OverridesStore(nested)
    store.save({"x": {"temp": 0.5}})
    assert nested.is_file()


def test_empty_file_loads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "overrides.yaml"
    path.write_text("")
    store = OverridesStore(path)
    assert store.load() == {}
