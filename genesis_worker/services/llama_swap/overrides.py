"""Per-model user overrides on top of recipe defaults."""

from __future__ import annotations

from pathlib import Path

import yaml


class OverridesStore:
    """Read/write ``overrides.yaml``.

    The store is intentionally tiny: load returns a dict, save writes
    a dict. Validation, field-level merging, and "is this a valid
    override for this entry" all live one layer up (in ``config.py``).
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        """Return ``{entry_id: {field: value, ...}}``. Missing file = empty."""
        if not self.path.is_file():
            return {}
        raw = yaml.safe_load(self.path.read_text()) or {}
        return raw.get("entries", {})

    def save(self, entries: dict[str, dict]) -> None:
        """Write the overrides dict back to disk. Creates parent dirs."""
        payload = {"entries": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(payload, sort_keys=False))


__all__ = ["OverridesStore"]
