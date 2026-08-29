"""Catalog on-disk I/O — load from JSON, save with no-op skip, atomic write.

The catalog is persisted at ``state_dir/catalog.json``. See ADR-011 for why
and what stable identity (``content_hash``) buys us.

The save path is intentionally cheap to call: it does a text-diff against the
existing file and only writes when content actually differs. The write itself
is atomic via a sibling temp file + ``os.replace`` so two streamlit sessions
racing on startup don't truncate each other.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..contracts import Catalog


def load_catalog(path: Path) -> Catalog | None:
    """Load and validate the persisted catalog. Returns ``None`` on any failure.

    Missing file, malformed JSON, schema-version mismatch — all return ``None``
    so the caller can rebuild from scratch.
    """
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return None
    try:
        catalog = Catalog.model_validate_json(text)
    except (ValueError, TypeError):
        return None
    if catalog.schema_version != Catalog.model_fields["schema_version"].default:
        return None
    return catalog


def save_catalog(path: Path, catalog: Catalog) -> bool:
    """Persist the catalog atomically. Returns True iff a write happened.

    Text-diff skip: when the would-be content matches the existing file, no
    write is performed. This keeps ``mtime`` stable for downstream tools
    (notably llama-swap's ``-watch-config``).
    """
    text = catalog.model_dump_json(indent=2)
    try:
        if path.read_text() == text:
            return False
    except (FileNotFoundError, OSError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return True


__all__ = ["load_catalog", "save_catalog"]
