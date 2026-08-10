"""Per-model user overrides on top of recipe defaults.

Stored in ``overrides.yaml``, keyed by llama-swap entry-id. Schema:

    entries:
      <entry-id>:
        sampling:
          temp: 0.6
        reasoning_budget: 8192

Merging precedence at build time (lowest -> highest):

    1. matched recipe
    2. default recipe
    3. overrides.yaml
    4. CLI --binary (binary path only; post-v1)

Missing file = empty store. Removing a field from overrides.yaml
clears that override (no tombstone needed).

ADR-007 details the file format and the SQLite deferral.
"""

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
