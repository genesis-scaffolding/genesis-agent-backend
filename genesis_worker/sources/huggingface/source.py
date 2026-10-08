"""HuggingFace cache walker."""

from __future__ import annotations

from pathlib import Path

from ...contracts import (
    SKIP_FILENAMES,
    AcquireSession,
    AcquireStateKind,
    DiscoveredModel,
    ModelPiece,
    ModelSource,
    SourceContext,
    UiPage,
    classify,
    role_sort_key,
)
from .acquire import HfAcquireSession, HfAcquireState
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
            hf_state=HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id=repo_id),
            cache_dir=self.local_path,
            revision=self._options.default_revision,
        )

    @property
    def ui_pages(self) -> list[UiPage]:
        ui_dir = Path(__file__).parent / "ui"
        return [
            UiPage("Acquire model", ":material/cloud_download:", ui_dir / "acquire.py"),
            UiPage("Active sessions", ":material/schedule:", ui_dir / "session_list.py"),
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

            snapshots_dir = repo_dir / "snapshots"
            if not snapshots_dir.is_dir():
                continue

            # Merge pieces across all snapshot revisions into one model entry.
            # Dedupe by resolved blob path: the same file can appear under the
            # same relative name in multiple revisions (e.g. a re-uploaded
            # weight) and we only want to count it once.
            all_pieces: list[ModelPiece] = []
            seen_paths: set[Path] = set()
            snapshot_shas: list[str] = []
            for snapshot_dir in sorted(snapshots_dir.iterdir()):
                if not snapshot_dir.is_dir():
                    continue
                snapshot_shas.append(snapshot_dir.name)
                pieces, _ = _collect_pieces(snapshot_dir)
                for piece in pieces:
                    if piece.path in seen_paths:
                        continue
                    seen_paths.add(piece.path)
                    all_pieces.append(piece)
            if not all_pieces:
                continue

            total_bytes = sum(p.bytes for p in all_pieces)
            main_snapshot = snapshot_shas[0] if snapshot_shas else None
            # directory is the whole repo: the entry now spans all revisions,
            # so "delete model" (facade.delete_model) wipes the entire repo.
            directory = repo_dir

            out.append(
                DiscoveredModel(
                    source="huggingface",
                    native_id=repo_id,
                    pieces=all_pieces,
                    total_bytes=total_bytes,
                    directory=directory.resolve(),
                    notes=_summarize_notes(all_pieces),
                    extra={"snapshot": main_snapshot, "snapshots": snapshot_shas},
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
