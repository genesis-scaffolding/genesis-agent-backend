"""User-overrides read/write — flat dotenv + per-service JSON sidecar.

The flat file at ``<config_dir>/user-overrides.env`` is the framework
knob override layer (ADR-034); it carries ``KEY=VALUE`` pairs and
lives at the top of the Settings precedence chain.

The per-service JSON sidecar at
``<config_dir>/services/<name>.overrides.json`` carries structured
overrides for declarative services — specifically the map-typed
``extra_env`` and ``extra_mounts`` options that don't fit the flat
dotenv shape (ADR-036). The sidecar is JSON because map types
(nested dicts) can't be represented as ``KEY=VALUE`` without
escaping.

Both files coexist: scalars flow through ``user-overrides.env`` for
backward compatibility with the existing Settings page; maps flow
through the sidecar. ``Settings.options_for("services", name)``
merges both sources.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_FILE_MODE = 0o600

# Directory under ``config_dir`` that holds per-service override sidecars.
SERVICE_OVERRIDES_DIRNAME = "services"


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


# --- per-service override JSON sidecar (ADR-036) ---------------------------


def service_overrides_path(config_dir: Path, name: str) -> Path:
    """Path to the per-service override JSON sidecar for ``name``.

    Lives under ``<config_dir>/services/<name>.overrides.json``. The
    ``services/`` directory is created on first write so the user
    never has to mkdir it themselves.
    """
    return config_dir / SERVICE_OVERRIDES_DIRNAME / f"{name}.overrides.json"


def read_service_overrides(path: Path) -> dict[str, Any]:
    """Parse the per-service overrides JSON sidecar. Missing file → empty dict.

    Malformed JSON raises with the parser's line / column so the
    caller can point the operator at the offending entry. Values
    are returned as-is (typed); ``Settings.options_for`` merges them
    into ``ctx.options`` so pydantic validates them at construction.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text()
    except OSError as exc:
        raise OSError(f"cannot read service overrides at {path}: {exc}") from exc
    try:
        loaded = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed service overrides at {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TypeError(
            f"malformed service overrides at {path}: expected a JSON object, got {type(loaded).__name__}"
        )
    return loaded


def write_service_overrides(path: Path, values: dict[str, Any]) -> None:
    """Atomic write of the per-service overrides JSON sidecar (mode 0o600).

    Writes through a sibling ``.tmp`` so a crash mid-write can't
    truncate the existing file. Sorted keys keep diffs stable across
    runs (``extra_env`` ordering doesn't matter to docker, but stable
    ordering makes the file pleasant to read and review).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(values, indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.chmod(_FILE_MODE)
    os.replace(tmp, path)


__all__ = [
    "SERVICE_OVERRIDES_DIRNAME",
    "read_service_overrides",
    "read_user_overrides",
    "service_overrides_path",
    "write_service_overrides",
    "write_user_overrides",
]
