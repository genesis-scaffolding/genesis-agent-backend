"""Catalog construction helpers — pure functions, no framework state."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ..contracts import Catalog, DiscoveredModel, ModelEntry


def build_catalog(discovered: list[DiscoveredModel], *, root: str) -> Catalog:
    entries = [
        ModelEntry(
            name=d.native_id,
            source=d.source,
            pieces=list(d.pieces),
            total_bytes=d.total_bytes,
            directory=str(d.directory),
            notes=list(d.notes),
            extra=dict(d.extra),
        )
        for d in discovered
    ]
    return Catalog(
        root=root,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        content_hash=compute_content_hash(entries),
        entries=entries,
    )


def compute_content_hash(entries: list[ModelEntry]) -> str:
    """Deterministic sha256-hex over the catalog's content-bearing fields.

    Excludes ``directory``, ``notes``, and ``extra`` because those don't
    affect what llama-swap would generate. Includes piece-level
    ``role`` / ``filename`` / ``bytes`` so file additions and deletions
    flip the hash.
    """
    norm = []
    for e in sorted(entries, key=lambda x: (x.source, x.name)):
        pieces = sorted(
            ((p.role, p.filename, p.bytes) for p in e.pieces),
            key=lambda t: (t[1], t[0], t[2]),
        )
        norm.append([e.source, e.name, e.total_bytes, pieces])
    blob = json.dumps(norm, sort_keys=False, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


__all__ = ["build_catalog", "compute_content_hash"]
