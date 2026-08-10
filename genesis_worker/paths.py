"""Path resolution for the worker."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Auto-detect the repo root from this file's location.

    Walks upward from ``Path(__file__).parent`` until it finds a directory
    containing ``pyproject.toml`` or ``Makefile``. Used as the default
    location for legacy state files until they are migrated to XDG.

    Falls back to ``Path(__file__).parent`` if no marker is found (i.e.
    the package was installed as a wheel outside the source tree).
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() or (candidate / "Makefile").is_file():
            return candidate
    return here


def xdg_path(name: str, default_relative_to_home: str) -> Path:
    """XDG-compliant toolkit path.

    Honors ``$XDG_<name>_HOME`` if set; otherwise falls back to the
    canonical default relative to ``$HOME``. Appends ``genesis-worker``
    to whichever base is resolved.
    """
    base = os.environ.get(f"XDG_{name}_HOME")
    root = Path(base) if base else Path.home() / default_relative_to_home
    return root / "genesis-worker"


__all__ = ["repo_root", "xdg_path"]
