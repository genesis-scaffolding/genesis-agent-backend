"""LM Studio walker.

Walks ``<local_path>/<publisher>/<model-dir>/`` and emits one
:class:`DiscoveredModel` per ``<publisher>/<model-dir>``.

The framework constructs each source with a fully-resolved
``local_path`` (see :class:`~genesis_worker.sources._registry.SourceRegistry`).
This module does not import ``xdg_path`` or compute paths itself — it
declares its on-disk layout via ``vault_subdir = "lmstudio/models"``.

Walker logic lifted from ``bin/catalog.py:walk_lmstudio`` — behavior
identical, output type is a dataclass instead of a dict. Classification
helpers are shared via :mod:`genesis_worker.sources._classify`.

ADR-003: ``can_acquire`` is False — LM Studio is read-only here; users
add models by dropping files into the right directory.
"""

from __future__ import annotations

from pathlib import Path

from ..models import DiscoveredModel, ModelPiece
from ._classify import SKIP_FILENAMES, classify, role_sort_key


class LMSource:
    """LM Studio layout: ``<local_path>/<publisher>/<model-dir>/``."""

    name = "lmstudio"
    display_name = "LM Studio"
    can_acquire = False
    vault_subdir = "lmstudio/models"
    local_path: Path  # framework-assigned at construction

    def __init__(self, local_path: Path) -> None:
        self.local_path = local_path

    def is_available(self) -> bool:
        return self.local_path.is_dir()

    def walk(self) -> list[DiscoveredModel]:
        models_dir = self.local_path
        if not models_dir.is_dir():
            return []

        out: list[DiscoveredModel] = []
        for pub_dir in sorted(models_dir.iterdir()):
            if not pub_dir.is_dir():
                continue
            for model_dir in sorted(pub_dir.iterdir()):
                if not model_dir.is_dir():
                    continue

                pieces: list[ModelPiece] = []
                partial: list[str] = []
                total_bytes = 0

                for p in sorted(model_dir.iterdir()):
                    if not p.is_file():
                        continue
                    if p.name in SKIP_FILENAMES:
                        continue
                    if p.name.endswith(".part"):
                        partial.append(p.name)
                        continue
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    pieces.append(
                        ModelPiece(
                            role=classify(p),
                            filename=p.name,
                            path=p.resolve(),
                            bytes=size,
                        )
                    )
                    total_bytes += size

                pieces.sort(key=lambda piece: (role_sort_key(piece.role), piece.filename))

                notes: list[str] = []
                if not any(p.role != "config" for p in pieces):
                    notes.append("no model weights on disk")
                if partial:
                    names = ", ".join(partial)
                    notes.append(f"partial download in progress (skipped): {names}")

                out.append(
                    DiscoveredModel(
                        source="lmstudio",
                        native_id=f"{pub_dir.name}/{model_dir.name}",
                        pieces=pieces,
                        total_bytes=total_bytes,
                        directory=model_dir.resolve(),
                        notes=notes,
                        extra={"publisher": pub_dir.name},
                    )
                )

        return out


__all__ = ["LMSource"]
