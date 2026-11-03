"""Resolve an installed binary inside an extracted release tree."""

from __future__ import annotations

from pathlib import Path


def resolve_binary(
    install_root: Path,
    name: str,
    *,
    legacy_rel: str | None = None,
) -> Path | None:
    """Find ``name`` under ``install_root``, preferring ``legacy_rel``."""
    if legacy_rel:
        legacy = install_root / legacy_rel
        if legacy.is_file():
            return legacy
    if not name:
        return None
    for candidate in install_root.rglob(name):
        if candidate.is_file():
            return candidate
    return None


__all__ = ["resolve_binary"]
