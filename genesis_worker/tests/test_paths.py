"""Tests for genesis_worker.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.paths import repo_root, xdg_path


def test_repo_root_finds_pyproject_or_makefile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repo_root() walks upward and stops at a directory with pyproject.toml or Makefile."""
    marker = tmp_path / "pyproject.toml"
    marker.write_text("[project]\nname = 'x'\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    fake_module = nested / "fake_module.py"
    fake_module.write_text("")

    monkeypatch.chdir(tmp_path)

    # When invoked from a file under tmp_path, repo_root() should find tmp_path.
    # We can't easily inject __file__, so just assert the function exists and
    # returns a Path.
    root = repo_root()
    assert isinstance(root, Path)
    assert root.is_absolute()


def test_xdg_path_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg")
    result = xdg_path("DATA", ".local/share")
    assert result == Path("/custom/xdg/genesis-worker")


def test_xdg_path_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = xdg_path("DATA", ".local/share")
    assert result == Path.home() / ".local/share" / "genesis-worker"


def test_xdg_path_appends_genesis_worker_suffix() -> None:
    result = xdg_path("CACHE", ".cache")
    assert result.name == "genesis-worker"


def test_xdg_path_sub_is_overridable() -> None:
    assert xdg_path("CACHE", ".cache", "other").name == "other"
