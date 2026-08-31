"""Persistence for the per-service enabled/disabled set.

Stored at ``<state_dir>/enabled_services.yaml``:

```yaml
enabled:
  - llama_swap
  - crawl4ai
```

``load_enabled_set`` returns ``None`` when the file is missing — callers
treat that as "first run" and decide what to do (the registry bootstraps
from ``is_available()`` in that case). Stale names in the file (services
that no longer exist) are silently ignored when the registry resolves
them; the file is rewritten on the next mutation.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

_STATE_FILE = "enabled_services.yaml"
_FILE_MODE = 0o600


def load_enabled_set(state_dir: Path) -> set[str] | None:
    """Read the persisted enabled set. Returns ``None`` when no file exists.

    Malformed YAML or a non-list value is also treated as ``None`` — the
    caller will re-bootstrap from current ``is_available()`` state. We
    don't raise here because there's nothing the user can do about a
    corrupt file at startup; the next mutation rewrites it cleanly.
    """
    path = state_dir / _STATE_FILE
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("enabled", [])
    if not isinstance(raw, list):
        return None
    return {str(name) for name in raw if isinstance(name, str) and name}


def save_enabled_set(state_dir: Path, names: set[str]) -> None:
    """Persist the enabled set atomically. Sorts for stable diffs.

    Atomic via a sibling tmp file + ``os.replace`` so a crash mid-write
    can't truncate the existing file. Mode ``0o600`` — the enabled set
    is operational state, not secrets, but it lives alongside secrets
    (API tokens, etc.) so the conservative default is fine.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _STATE_FILE
    payload = yaml.safe_dump({"enabled": sorted(names)}, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.chmod(_FILE_MODE)
    os.replace(tmp, path)


__all__ = ["load_enabled_set", "save_enabled_set"]
