"""Compose env-file writer — the user's env map reaches the container.

A compose template can only name the environment variables it knows, so
the long tail of upstream env vars (ROMM's metadata providers, for
instance) has no place in it. The framework therefore writes the user's
``extra_env`` map to a file it manages and the template points at that
file with compose's ``env_file:`` (ADR-053).

The file is written in compose's own env-file syntax, so a value
round-trips exactly. Compose's parser (compose-go ``dotenv``) reads a
double-quoted value like a shell does:

    \\   -> backslash        \\"  -> double quote
    \\$  -> dollar sign      \\n  -> newline
    \\r  -> carriage return  \\t  -> tab

A single-quoted value is the odd one out: compose keeps its backslashes
verbatim (only ``\\'`` becomes a quote), so a password containing a
backslash would arrive mangled. Unknown escapes lose their backslash,
which is why every backslash is escaped here.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

_FILE_MODE = 0o600

# Characters that must not reach the file raw inside a quoted value.
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "$": "\\$",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

# An env name: what the shell (and compose) accepts as a variable name.
_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def quote_env_value(value: str) -> str:
    """Render ``value`` as a double-quoted compose env-file value."""
    return '"' + "".join(_ESCAPES.get(char, char) for char in value) + '"'


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write ``values`` to ``path`` as a compose env file.

    Mode 0600 — the map carries credentials. Atomic: a sibling tmp file
    plus ``os.replace``, so an interrupted write cannot leave compose
    with a half-written env file. The file is rewritten whole, so a key
    the user removed does not survive.
    """
    for key in values:
        if not key or not set(key) <= _NAME_CHARS or key[0].isdigit():
            raise ValueError(
                f"invalid environment variable name {key!r} for {path}; "
                "names must start with a letter or underscore and hold only "
                "letters, digits and underscores"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{key}={quote_env_value(value)}\n" for key, value in values.items())
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        with tmp.open("w") as handle:
            handle.write(payload)
        tmp.chmod(_FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


__all__ = ["quote_env_value", "write_env_file"]
