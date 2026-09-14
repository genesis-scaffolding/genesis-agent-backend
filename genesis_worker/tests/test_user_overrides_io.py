"""Tests for the user-overrides dotenv helper."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from genesis_worker.utils.config_overrides import read_user_overrides, write_user_overrides


def test_read_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert read_user_overrides(tmp_path / "absent.env") == {}


def test_read_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    path.write_text(
        "\n"
        "# a header comment\n"
        "GENESIS_PATHS__VAULT_PATH=/srv/vault\n"
        "\n"
        "# inline comment\n"
        "MODELS_ROOT=/srv/models\n"
    )
    assert read_user_overrides(path) == {
        "GENESIS_PATHS__VAULT_PATH": "/srv/vault",
        "MODELS_ROOT": "/srv/models",
    }


def test_read_strips_value_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    path.write_text("KEY=   trailing-spaces   \n")
    assert read_user_overrides(path) == {"KEY": "trailing-spaces"}


def test_read_malformed_line_raises(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    path.write_text("KEY1=value1\nNOKEY\nKEY2=value2\n")
    with pytest.raises(ValueError, match="malformed override"):
        read_user_overrides(path)


def test_read_empty_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    path.write_text("=value\n")
    with pytest.raises(ValueError, match="empty key"):
        read_user_overrides(path)


def test_round_trip_preserves_values(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    values = {
        "GENESIS_PATHS__VAULT_PATH": "/srv/vault",
        "MODELS_ROOT": "/srv/models",
        "GENESIS_SOURCES__HUGGINGFACE__LOCAL_PATH": "/srv/hf",
    }
    write_user_overrides(path, values)
    assert read_user_overrides(path) == values


def test_write_is_atomic_and_0o600(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    write_user_overrides(path, {"KEY": "value"})
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # No stray .tmp left behind after a successful write.
    assert not (path.with_suffix(path.suffix + ".tmp")).exists()


def test_write_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    write_user_overrides(path, {"OLD": "stale"})
    write_user_overrides(path, {"NEW": "fresh"})
    assert read_user_overrides(path) == {"NEW": "fresh"}
    assert "OLD" not in path.read_text()


def test_write_sorts_keys_for_stable_diffs(tmp_path: Path) -> None:
    path = tmp_path / "overrides.env"
    write_user_overrides(
        path,
        {"ZULU": "z", "ALPHA": "a", "MIKE": "m"},
    )
    text = path.read_text()
    lines = text.strip().splitlines()
    assert lines == ["ALPHA=a", "MIKE=m", "ZULU=z"]


def test_write_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "overrides.env"
    write_user_overrides(path, {"KEY": "value"})
    assert path.is_file()
