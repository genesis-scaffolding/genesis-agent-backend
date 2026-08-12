"""Parse a llama-server command string into structured (flag, value) pairs.

The cmd lives in config.yaml's ``cmd:`` field as a YAML literal block
with backslash continuations. We join the lines, shlex-split, and walk
the tokens: ``--flag value`` -> (flag, value); ``--flag`` alone -> (flag, True).
The original flag token (including any leading dashes) is preserved so
display can distinguish short (``-ctk``) from long (``--model``) flags.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCommand:
    binary: str
    flags: list[tuple[str, str | bool]]


def parse_cmd(cmd: str) -> ParsedCommand:
    joined = cmd.replace("\\\n", " ")
    tokens = shlex.split(joined)
    if not tokens:
        return ParsedCommand(binary="", flags=[])
    binary = tokens[0]
    flags: list[tuple[str, str | bool]] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                flags.append((tok, tokens[i + 1]))
                i += 2
            else:
                flags.append((tok, True))
                i += 1
        else:
            i += 1
    return ParsedCommand(binary=binary, flags=flags)


__all__ = ["ParsedCommand", "parse_cmd"]