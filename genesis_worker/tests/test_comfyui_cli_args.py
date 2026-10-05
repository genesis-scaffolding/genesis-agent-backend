"""Tests for ``comfyui.cli_args.parse_extra_args`` and ``parse_extra_env``."""

from __future__ import annotations

from genesis_worker.services.comfyui.cli_args import parse_extra_args, parse_extra_env


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


# --- parse_extra_env -------------------------------------------------------


def test_env_basic_key_value() -> None:
    assert parse_extra_env("HF_HOME=/cache") == {"HF_HOME": "/cache"}


def test_env_multiple_vars() -> None:
    raw = "HF_HOME=/cache\nHF_TOKEN=hf_abc\nCOMFYUI_PORT=8188"
    assert parse_extra_env(raw) == {
        "HF_HOME": "/cache",
        "HF_TOKEN": "hf_abc",
        "COMFYUI_PORT": "8188",
    }


def test_env_empty_value_preserved() -> None:
    """``FOO=`` is preserved as empty string (clears inherited default)."""
    assert parse_extra_env("FOO=") == {"FOO": ""}


def test_env_quoted_value_with_spaces_strips_quotes() -> None:
    """``FOO=\"hello world\"`` → ``{"FOO": "hello world"}``."""
    assert parse_extra_env('FOO="hello world"') == {"FOO": "hello world"}


def test_env_unquoted_value_with_spaces_preserved() -> None:
    """Unquoted spaces in the value are part of the value, not split."""
    assert parse_extra_env("FOO=hello world") == {"FOO": "hello world"}


def test_env_single_quotes_also_stripped() -> None:
    assert parse_extra_env("FOO='hello world'") == {"FOO": "hello world"}


def test_env_blank_lines_and_comments_skipped() -> None:
    raw = "\n# a comment\nHF_HOME=/cache\n# another\n\nFOO=bar"
    assert parse_extra_env(raw) == {"HF_HOME": "/cache", "FOO": "bar"}


def test_env_lines_without_equals_are_skipped() -> None:
    """A bare key with no value is silently ignored (no implicit empty)."""
    raw = "HF_HOME=/cache\nNOT_A_VALID_LINE\nFOO=bar"
    assert parse_extra_env(raw) == {"HF_HOME": "/cache", "FOO": "bar"}


def test_env_empty_input() -> None:
    assert parse_extra_env("") == {}
    assert parse_extra_env("\n\n# only comments\n") == {}


def test_env_value_with_equals_in_it() -> None:
    """``partition`` on the first ``=`` so a literal ``=`` in the value survives."""
    assert parse_extra_env("CONNECTION=host=db;port=5432") == {"CONNECTION": "host=db;port=5432"}


def test_env_whitespace_around_key_stripped() -> None:
    assert parse_extra_env("  HF_HOME  =/cache") == {"HF_HOME": "/cache"}
