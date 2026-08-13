"""Tests for InstallLayout: installs/, current symlink, selections.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.utils.install import InstallLayout


def _layout(tmp_path: Path, name: str = "llama-swap") -> InstallLayout:
    return InstallLayout(tmp_path / "data", tmp_path / "state", name)


def test_empty(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert layout.resolved_selection() is None


def test_current_symlink_resolves(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.installs_root / "v0.4.5").mkdir(parents=True)
    layout.current_symlink.symlink_to("v0.4.5")
    assert layout.resolved_selection() == "v0.4.5"


def test_multiple_installs_only_current_wins(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.installs_root / "v0.4.4").mkdir(parents=True)
    (layout.installs_root / "v0.4.5").mkdir(parents=True)
    layout.current_symlink.symlink_to("v0.4.5")
    assert layout.resolved_selection() == "v0.4.5"


def test_pinning_overrides_current(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.installs_root / "v0.4.4").mkdir(parents=True)
    (layout.installs_root / "v0.4.5").mkdir(parents=True)
    layout.current_symlink.symlink_to("v0.4.5")
    layout.selections_path.parent.mkdir(parents=True, exist_ok=True)
    layout.selections_path.write_text("llama-swap: v0.4.4\n")
    assert layout.resolved_selection() == "v0.4.4"


def test_pin_to_uninstalled_falls_through(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.installs_root / "v0.4.5").mkdir(parents=True)
    layout.current_symlink.symlink_to("v0.4.5")
    layout.selections_path.parent.mkdir(parents=True, exist_ok=True)
    layout.selections_path.write_text("llama-swap: v0.0.1\n")
    assert layout.resolved_selection() == "v0.4.5"


def test_set_selection_writes_yaml(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.installs_root / "v0.4.4").mkdir(parents=True)
    layout.set_selection("v0.4.4")
    assert layout.selections_path.is_file()
    assert layout.resolved_selection() == "v0.4.4"


def test_set_current_symlink_atomic_swap(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.installs_root / "v0.4.4").mkdir(parents=True)
    (layout.installs_root / "v0.4.5").mkdir(parents=True)

    layout.set_current_symlink("v0.4.4")
    assert layout.current_symlink.is_symlink()
    assert layout.current_symlink.readlink() == Path("v0.4.4")
    assert layout.resolved_selection() == "v0.4.4"

    layout.set_current_symlink("v0.4.5")
    assert layout.current_symlink.readlink() == Path("v0.4.5")
    assert layout.resolved_selection() == "v0.4.5"


def test_set_current_symlink_rejects_missing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(ValueError, match="not installed"):
        layout.set_current_symlink("v0.0.1")


def test_set_selection_rejects_uninstalled(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(ValueError, match="cannot pin to uninstalled"):
        layout.set_selection("v0.0.1")
