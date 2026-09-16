"""Tests for ``utils.ensure_persistent_file`` — read-or-create token file."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from genesis_worker.utils.ensure_persistent_file import (
    ensure_persistent_file,
    random_hex_32,
    random_urlsafe_32,
)


def test_returns_existing_value_when_file_present(tmp_path: Path) -> None:
    """An existing non-empty file is returned verbatim; the generator is not called."""
    target = tmp_path / "token"
    target.write_text("already-here\n")
    assert ensure_persistent_file(target) == "already-here"


def test_strips_whitespace_when_reading_existing(tmp_path: Path) -> None:
    target = tmp_path / "token"
    target.write_text("padded\n\n")
    assert ensure_persistent_file(target) == "padded"


def test_treats_empty_existing_file_as_absent(tmp_path: Path) -> None:
    """An empty file is regenerated — the persistence promise is "non-empty value"."""
    target = tmp_path / "token"
    target.write_text("")
    out = ensure_persistent_file(target)
    assert out and out == target.read_text().strip()


def test_writes_generator_value_when_file_absent(tmp_path: Path) -> None:
    target = tmp_path / "token"
    out = ensure_persistent_file(target, generator=lambda: "freshly-generated")
    assert out == "freshly-generated"
    assert target.read_text() == "freshly-generated"


def test_default_generator_is_random_hex_32(tmp_path: Path) -> None:
    target = tmp_path / "token"
    out = ensure_persistent_file(target)
    # 32 bytes hex = 64 chars.
    assert len(out) == 64
    # And it's actually a hex string.
    int(out, 16)


def test_alternate_generator_random_urlsafe_32(tmp_path: Path) -> None:
    target = tmp_path / "token"
    out = ensure_persistent_file(target, generator=random_urlsafe_32)
    # 32 bytes urlsafe = ~43 chars (no padding).
    assert len(out) >= 43


def test_creates_parent_directories(tmp_path: Path) -> None:
    """Missing parent dirs are created on demand."""
    target = tmp_path / "deep" / "nest" / "token"
    out = ensure_persistent_file(target, generator=lambda: "x")
    assert out == "x"
    assert target.is_file()


def test_applies_mode_atomically(tmp_path: Path) -> None:
    """The mode is set on the temp file before the rename, so the final file is correct."""
    target = tmp_path / "token"
    ensure_persistent_file(target, mode=0o640, generator=lambda: "y")
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o640


def test_default_mode_is_0o600(tmp_path: Path) -> None:
    target = tmp_path / "token"
    ensure_persistent_file(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_existing_file_mode_is_left_alone(tmp_path: Path) -> None:
    """We don't chmod files we didn't create — pre-existing tokens stay as the user set them."""
    target = tmp_path / "token"
    target.write_text("pre-existing")
    os.chmod(target, 0o644)
    ensure_persistent_file(target, mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_idempotent_returns_same_value_on_repeat(tmp_path: Path) -> None:
    """Calling twice yields the same value — the persistence is real."""
    target = tmp_path / "token"
    first = ensure_persistent_file(target, generator=lambda: "stable")
    second = ensure_persistent_file(target)
    assert first == second == "stable"


def test_two_targets_get_distinct_values(tmp_path: Path) -> None:
    """Two fresh targets are seeded independently."""
    a = ensure_persistent_file(tmp_path / "a", generator=lambda: "value-a")
    b = ensure_persistent_file(tmp_path / "b", generator=lambda: "value-b")
    assert a != b


def test_overwrite_with_different_generator(tmp_path: Path) -> None:
    """Replacing an empty file with a different generator updates the value."""
    target = tmp_path / "token"
    target.write_text("")
    out = ensure_persistent_file(target, generator=lambda: "replacement")
    assert out == "replacement"
    assert target.read_text() == "replacement"


def test_random_generators_produce_distinct_values() -> None:
    """Sanity check on the bundled generators — entropy is real."""
    assert random_hex_32() != random_hex_32()
    assert random_urlsafe_32() != random_urlsafe_32()
    with pytest.raises(TypeError):
        # generators take no args; passing one is a TypeError, not a silent bug.
        random_hex_32("extra")  # type: ignore[call-arg]
