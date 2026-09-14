"""Dotenv read/write for the user-overrides file.

Format: one ``KEY=VALUE`` per line, ``#`` for comments, blank lines
ignored. Hand-rolled rather than via python-dotenv because the file
is small and the format is trivial — no quoting edge cases, no
variable interpolation, no escaping.

Used at ``<config_dir>/user-overrides.env`` to layer user overrides
at the top of the Settings precedence chain (ADR-034).
"""

from __future__ import annotations

import os
from pathlib import Path

_FILE_MODE = 0o600


def read_user_overrides(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a dict. Missing file → empty dict.

    Malformed lines raise with the line number so the caller can
    point the operator at the offending entry. Lines look like
    ``KEY=VALUE``; the value runs to end of line with optional
    surrounding whitespace stripped. ``#`` at the start of a line
    is a comment.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise OSError(f"cannot read user overrides at {path}: {exc}") from exc

    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"malformed override at {path}:{lineno}: expected KEY=VALUE, got {raw!r}"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"malformed override at {path}:{lineno}: empty key in {raw!r}")
        out[key] = value.strip()
    return out


def write_user_overrides(path: Path, values: dict[str, str]) -> None:
    """Atomic write with mode ``0o600``. Sorted keys for stable diffs.

    A crash mid-write can't truncate the existing file: writes land
    in a sibling ``.tmp`` and ``os.replace`` swaps it into place.
    Mode matches ``enabled_services.yaml`` — operational state, not
    secrets, but it lives next to secrets on disk so the conservative
    default is fine.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(f"{k}={values[k]}" for k in sorted(values)) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.chmod(_FILE_MODE)
    os.replace(tmp, path)


__all__ = ["read_user_overrides", "write_user_overrides"]
