"""HuggingFace cache walker."""

from __future__ import annotations

from pathlib import Path

from ...contracts import (
    SKIP_FILENAMES,
    AcquireSession,
    AcquireState,
    DiscoveredModel,
    ModelPiece,
    ModelSource,
    SourceContext,
    UiPage,
    classify,
    role_sort_key,
)
from .acquire import HfAcquireSession
from .options import HuggingFaceOptions


class HuggingFaceSource(ModelSource):
    """HuggingFace cache layout: ``<local_path>/models--org--repo/``."""

    name = "huggingface"
    display_name = "HuggingFace"
    can_acquire = True
    vault_subdir = "huggingface/hub"

    def __init__(self, ctx: SourceContext) -> None:
        super().__init__(ctx)
        self._options = HuggingFaceOptions(**ctx.options)

    def start_acquire(self, repo_id: str) -> AcquireSession:
        from huggingface_hub import HfApi

        return HfAcquireSession(
            api=HfApi(),
            state=AcquireState(source=self.name, repo_id=repo_id),
            cache_dir=self.local_path,
            revision=self._options.default_revision,
        )

    @property
    def ui_pages(self) -> list[UiPage]:
        ui_dir = Path(__file__).parent / "ui"
        return [
            UiPage("Acquire model",   ":material/cloud_download:", ui_dir / "acquire.py"),
            UiPage("Active sessions", ":material/schedule:",       ui_dir / "session_list.py"),
        ]

    def is_available(self) -> bool:
        return self.local_path.is_dir()

    def walk(self) -> list[DiscoveredModel]:
        hub_dir = self.local_path
        if not hub_dir.is_dir():
            return []

        out: list[DiscoveredModel] = []
        for repo_dir in sorted(hub_dir.iterdir()):
            if not repo_dir.is_dir() or not repo_dir.name.startswith("models--"):
                continue

            # models--org--repo -> org/repo. Repo name itself may contain "--"
            # in theory, but in practice the org is single-segment.
            parts = repo_dir.name.split("--")
            if len(parts) < 3:
                continue
            repo_id = f"{parts[1]}/{'--'.join(parts[2:])}"

            refs_main = repo_dir / "refs" / "main"
            snapshots_dir = repo_dir / "snapshots"
            if not refs_main.is_file() or not snapshots_dir.is_dir():
                continue

            try:
                sha = refs_main.read_text().strip()
            except OSError:
                continue

            snapshot_dir = snapshots_dir / sha
            if not snapshot_dir.is_dir():
                continue

            pieces, total_bytes = _collect_pieces(snapshot_dir)
            notes = _summarize_notes(pieces)

            out.append(
                DiscoveredModel(
                    source="huggingface",
                    native_id=repo_id,
                    pieces=pieces,
                    total_bytes=total_bytes,
                    directory=snapshot_dir.resolve(),
                    notes=notes,
                    extra={"snapshot": sha},
                )
            )

        return out


def _collect_pieces(snapshot_dir: Path) -> tuple[list[ModelPiece], int]:
    pieces: list[ModelPiece] = []
    total_bytes = 0

    for p in sorted(snapshot_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name in SKIP_FILENAMES:
            continue
        # Resolve symlinks to get the real blob path and real size.
        real = p.resolve()
        try:
            size = real.stat().st_size
        except OSError:
            continue
        pieces.append(
            ModelPiece(
                role=classify(p),
                filename=str(p.relative_to(snapshot_dir)),
                path=real,
                bytes=size,
            )
        )
        total_bytes += size

    pieces.sort(key=lambda piece: (role_sort_key(piece.role), piece.filename))
    return pieces, total_bytes


def _summarize_notes(pieces: list[ModelPiece]) -> list[str]:
    notes: list[str] = []
    if not any(p.role != "config" for p in pieces):
        notes.append("no model weights on disk")
    return notes


__all__ = ["HuggingFaceSource"]
