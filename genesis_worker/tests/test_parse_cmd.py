"""Tests for parse_cmd — structured view of llama-server command strings."""

from __future__ import annotations

from genesis_worker.services.llama_swap.parse_cmd import ParsedCommand, parse_cmd


def test_parse_cmd_empty() -> None:
    assert parse_cmd("") == ParsedCommand(binary="", flags=[])


def test_parse_cmd_binary_only() -> None:
    assert parse_cmd("/path/to/llama-server") == ParsedCommand(
        binary="/path/to/llama-server",
        flags=[],
    )


def test_parse_cmd_flag_with_value() -> None:
    parsed = parse_cmd("/bin/server --model /foo.gguf")
    assert parsed == ParsedCommand(
        binary="/bin/server",
        flags=[("--model", "/foo.gguf")],
    )


def test_parse_cmd_boolean_flag() -> None:
    parsed = parse_cmd("/bin/server --jinja")
    assert parsed == ParsedCommand(
        binary="/bin/server",
        flags=[("--jinja", True)],
    )


def test_parse_cmd_preserves_short_flag_form() -> None:
    parsed = parse_cmd("/bin/server -ctk q8_0 -ctv q8_0 -fa on")
    assert parsed == ParsedCommand(
        binary="/bin/server",
        flags=[("-ctk", "q8_0"), ("-ctv", "q8_0"), ("-fa", "on")],
    )


def test_parse_cmd_joins_backslash_continuations() -> None:
    cmd = (
        "/bin/server \\\n"
        "  --model /foo.gguf \\\n"
        "  --fit-ctx 131072 \\\n"
        "  --jinja\n"
    )
    parsed = parse_cmd(cmd)
    assert parsed == ParsedCommand(
        binary="/bin/server",
        flags=[
            ("--model", "/foo.gguf"),
            ("--fit-ctx", "131072"),
            ("--jinja", True),
        ],
    )


def test_parse_cmd_preserves_flag_order() -> None:
    parsed = parse_cmd("/bin/server --z last --a first --m middle")
    assert [flag for flag, _ in parsed.flags] == ["--z", "--a", "--m"]


def test_parse_cmd_handles_single_quoted_value() -> None:
    parsed = parse_cmd("/bin/server --reasoning-budget-message 'Final answer:'")
    assert parsed.flags == [("--reasoning-budget-message", "Final answer:")]


def test_parse_cmd_handles_json_kwargs() -> None:
    parsed = parse_cmd(
        "/bin/server --chat-template-kwargs '{\"preserve_thinking\":true}'"
    )
    assert parsed.flags == [
        ("--chat-template-kwargs", '{"preserve_thinking":true}'),
    ]


def test_parse_cmd_ignores_stray_positional_arg() -> None:
    # Shouldn't happen in practice, but defend against malformed cmds.
    parsed = parse_cmd("/bin/server --flag stray_pos")
    assert parsed.flags == [("--flag", "stray_pos")]