"""Tests for ``comfyui.cli_args.parse_extra_args``."""

from __future__ import annotations

from genesis_worker.services.comfyui.cli_args import parse_extra_args


def test_single_flag_per_line_stays_single_token() -> None:
    """Each line is one argv element when there's no value to split off."""
    assert parse_extra_args("--verbose") == ["--verbose"]
    assert parse_extra_args("--gpu-only") == ["--gpu-only"]


def test_flag_with_value_splits_into_two_tokens() -> None:
    """``--reserve-vram 10`` on one line must become two argv tokens.

    Otherwise docker passes it as one argv element (with a literal
    space) and ComfyUI's argparse either errors or treats the next
    flag as the value of the previous one.
    """
    assert parse_extra_args("--reserve-vram 10") == ["--reserve-vram", "10"]


def test_flag_with_equals_sign_stays_single_token() -> None:
    """``--key=value`` is one argv element per POSIX rules."""
    assert parse_extra_args("--reserve-vram=10") == ["--reserve-vram=10"]


def test_multiple_flags_one_per_line() -> None:
    raw = "--verbose\n--gpu-only\n--disable-metadata"
    assert parse_extra_args(raw) == ["--verbose", "--gpu-only", "--disable-metadata"]


def test_mix_of_one_and_two_token_lines() -> None:
    """Realistic edit: most flags are bare, one or two carry a value."""
    raw = "--verbose\n--reserve-vram 10\n--cache-lru 8\n--gpu-only"
    assert parse_extra_args(raw) == [
        "--verbose",
        "--reserve-vram",
        "10",
        "--cache-lru",
        "8",
        "--gpu-only",
    ]


def test_blank_lines_are_skipped() -> None:
    raw = "--verbose\n\n\n--gpu-only\n   \n"
    assert parse_extra_args(raw) == ["--verbose", "--gpu-only"]


def test_quoted_value_with_spaces() -> None:
    """shlex handles quoted values so flags with embedded spaces still work."""
    raw = '--name "my workflow"'
    assert parse_extra_args(raw) == ["--name", "my workflow"]


def test_empty_input() -> None:
    assert parse_extra_args("") == []
    assert parse_extra_args("\n\n\n") == []


def test_user_reported_repro() -> None:
    """The exact case the user hit: two flags, one with a value."""
    raw = "--verbose\n--reserve-vram 10"
    result = parse_extra_args(raw)
    # The user's container error was on --verbose receiving
    # "--reserve-vram 10" as its value; with the fix, "--reserve-vram"
    # and "10" are separate argv tokens that ComfyUI's argparse
    # consumes correctly.
    assert result == ["--verbose", "--reserve-vram", "10"]
    # Specifically: no element contains a literal space.
    for tok in result:
        assert " " not in tok
