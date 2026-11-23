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

The file is the authority for the set (ADR-054): every process re-reads it
when its identity changes, so a flip in one process is visible in the
other on the next read.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

_STATE_FILE = "enabled_services.yaml"
_FILE_MODE = 0o600

# The identity of one published version of the file. ``os.replace`` lands a
# fresh inode on every write, so the identity changes even when the content
# and the timestamp do not.
FileKey = tuple[int, int, int]


@dataclass(frozen=True)
class EnabledSetSnapshot:
    """One version of the enabled set, read through a single descriptor."""

    key: FileKey | None
    names: frozenset[str]
    readable: bool


def enabled_set_path(state_dir: Path) -> Path:
    return state_dir / _STATE_FILE


def enabled_set_key(state_dir: Path) -> FileKey | None:
    """The identity of the file's current version, or ``None`` when absent."""
    try:
        st = os.stat(enabled_set_path(state_dir))
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def read_enabled_snapshot(state_dir: Path) -> EnabledSetSnapshot:
    """Read the file through one open descriptor and stat that descriptor.

    The identity recorded in the snapshot belongs to the bytes that were
    parsed, so a reader never mixes two versions of the file.
    """
    try:
        with open(enabled_set_path(state_dir), encoding="utf-8") as fh:
            st = os.fstat(fh.fileno())
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return EnabledSetSnapshot(key=None, names=frozenset(), readable=False)
    key: FileKey = (st.st_ino, st.st_mtime_ns, st.st_size)
    names = _parse_names(text)
    if names is None:
        return EnabledSetSnapshot(key=key, names=frozenset(), readable=False)
    return EnabledSetSnapshot(key=key, names=frozenset(names), readable=True)


def _parse_names(text: str) -> set[str] | None:
    """The names in a payload, or ``None`` when the payload is not a set."""
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


def load_enabled_set(state_dir: Path) -> set[str] | None:
    """Read the persisted enabled set. Returns ``None`` when no file exists.

    Malformed YAML or a non-list value is also treated as ``None`` — the
    caller will re-bootstrap from current ``is_available()`` state. We
    don't raise here because there's nothing the user can do about a
    corrupt file at startup; the next mutation rewrites it cleanly.

    No production caller: the registry reads through
    :func:`read_enabled_snapshot` (ADR-054). Kept as the one-shot
    accessor for callers that want the whole set.
    """
    snapshot = read_enabled_snapshot(state_dir)
    if not snapshot.readable:
        return None
    return set(snapshot.names)


def save_enabled_set(state_dir: Path, names: set[str]) -> None:
    """Persist the enabled set atomically. Sorts for stable diffs.

    Atomic via a sibling tmp file + ``os.replace`` so a crash mid-write
    can't truncate the existing file. Mode ``0o600`` — the enabled set
    is operational state, not secrets, but it lives alongside secrets
    (API tokens, etc.) so the conservative default is fine.

    The tmp name is unique per write: the worker runs as two processes
    that both write this file (ADR-054), and a shared tmp path would let
    one process publish the other's half-written bytes.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = enabled_set_path(state_dir)
    payload = yaml.safe_dump({"enabled": sorted(names)}, sort_keys=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(payload)
        tmp.chmod(_FILE_MODE)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = [
    "EnabledSetSnapshot",
    "FileKey",
    "enabled_set_key",
    "enabled_set_path",
    "load_enabled_set",
    "read_enabled_snapshot",
    "save_enabled_set",
]
