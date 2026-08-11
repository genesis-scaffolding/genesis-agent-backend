"""LM Studio walker."""

from __future__ import annotations

from ...contracts import (
    SKIP_FILENAMES,
    DiscoveredModel,
    ModelPiece,
    ModelSource,
    classify,
    role_sort_key,
)


class LMSource(ModelSource):
    """LM Studio layout: ``<local_path>/<publisher>/<model-dir>/``."""

    name = "lmstudio"
    display_name = "LM Studio"
    can_acquire = False
    vault_subdir = "lmstudio/models"

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
