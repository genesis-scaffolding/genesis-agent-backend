"""Tests for ``SymlinkApplier`` — yaml registry + filesystem reconciliation."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
from genesis_worker.services.comfyui.symlinks import (
    ApplyResult,
    PruneResult,
    SymlinkApplier,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_catalog(pieces: list[tuple[str, str, str, Path]] | None = None) -> Catalog:
    """Build a catalog from a list of (source, entry_name, piece_filename, piece_path).

    Each tuple produces one catalog entry containing one piece.
    """
    if pieces is None:
        pieces = []
    by_src: dict[str, list[ModelEntry]] = {}
    for source, name, piece_filename, piece_path in pieces:
        entry = ModelEntry(
            name=name,
            source=source,
            pieces=[
                ModelPiece(
                    role="main",
                    filename=piece_filename,
                    path=piece_path,
                    bytes=piece_path.stat().st_size if piece_path.exists() else 0,
                )
            ],
            total_bytes=0,
            directory=str(piece_path.parent),
            notes=[],
            extra={},
        )
        by_src.setdefault(source, []).append(entry)
    entries: list[ModelEntry] = []
    for src_entries in by_src.values():
        entries.extend(src_entries)
    return Catalog(
        schema_version=1,
        root="/vault",
        generated_at="2026-01-01T00:00:00Z",
        content_hash="x",
        entries=entries,
    )


def _make_blobs(tmp_path: Path, names: list[str]) -> dict[str, Path]:
    """Create empty source files at ``<tmp>/blobs/<name>``; return ``{name: path}``."""
    blobs_dir = tmp_path / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name in names:
        p = blobs_dir / name
        p.write_bytes(b"x" * 8)
        out[name] = p
    return out


def _make_applier(tmp_path: Path) -> SymlinkApplier:
    return SymlinkApplier(
        symlinks_file=tmp_path / "config" / "comfyui" / "model_symlink.yaml",
        vault_models_dir=tmp_path / "vault" / "comfyui",
    )


# --- yaml io ---------------------------------------------------------------


def test_yaml_missing_returns_empty(tmp_path: Path) -> None:
    applier = _make_applier(tmp_path)
    assert applier.list_current(_make_catalog([])) == []


def test_yaml_malformed_returns_empty(tmp_path: Path) -> None:
    symlinks_file = tmp_path / "config" / "comfyui" / "model_symlink.yaml"
    symlinks_file.parent.mkdir(parents=True, exist_ok=True)
    symlinks_file.write_text("not: valid: yaml: at: all:")
    applier = SymlinkApplier(
        symlinks_file=symlinks_file,
        vault_models_dir=tmp_path / "vault" / "comfyui",
    )
    assert applier.list_current(_make_catalog([])) == []


def test_yaml_wrong_version_returns_empty(tmp_path: Path) -> None:
    symlinks_file = tmp_path / "config" / "comfyui" / "model_symlink.yaml"
    symlinks_file.parent.mkdir(parents=True, exist_ok=True)
    symlinks_file.write_text("version: 999\nsymlinks: []\n")
    applier = SymlinkApplier(
        symlinks_file=symlinks_file,
        vault_models_dir=tmp_path / "vault" / "comfyui",
    )
    assert applier.list_current(_make_catalog([])) == []


# --- add / remove / list_current ------------------------------------------


def test_add_appends_rows(tmp_path: Path) -> None:
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([])
    errors = applier.add(
        [
            {"source": "huggingface", "entry": "Org/Repo1", "piece": "x.safetensors", "target_subdir": "checkpoints"},
            {"source": "huggingface", "entry": "Org/Repo2", "piece": "y.safetensors", "target_subdir": "loras"},
        ]
    )
    assert errors == []
    rows = applier.list_current(catalog)
    assert len(rows) == 2


def test_add_rejects_duplicate(tmp_path: Path) -> None:
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([])
    applier.add(
        [{"source": "huggingface", "entry": "Org/Repo1", "piece": "x.safetensors", "target_subdir": "checkpoints"}]
    )
    errors = applier.add(
        [{"source": "huggingface", "entry": "Org/Repo1", "piece": "x.safetensors", "target_subdir": "checkpoints"}]
    )
    assert len(errors) == 1
    assert "duplicate" in errors[0]
    assert len(applier.list_current(catalog)) == 1


def test_add_rejects_invalid_rows(tmp_path: Path) -> None:
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([])
    mixed_input: list[object] = [
        {"source": "huggingface"},
        "not a dict",
        {"source": "huggingface", "entry": "a/b", "piece": "x.safetensors", "target_subdir": "checkpoints"},
    ]
    errors = applier.add(mixed_input)  # type: ignore[arg-type]
    assert len(errors) == 2
    assert len(applier.list_current(catalog)) == 1


def test_remove_drops_rows(tmp_path: Path) -> None:
    blobs = _make_blobs(tmp_path, ["x.safetensors", "y.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog(
        [
            ("huggingface", "Org/Repo1", "x.safetensors", blobs["x.safetensors"]),
            ("huggingface", "Org/Repo2", "y.safetensors", blobs["y.safetensors"]),
        ]
    )
    applier.add(
        [
            {"source": "huggingface", "entry": "Org/Repo1", "piece": "x.safetensors", "target_subdir": "checkpoints"},
            {"source": "huggingface", "entry": "Org/Repo2", "piece": "y.safetensors", "target_subdir": "loras"},
        ]
    )
    rows = applier.list_current(catalog)
    assert len(rows) == 2
    applier.remove([rows[0]])
    assert len(applier.list_current(catalog)) == 1


# --- apply ----------------------------------------------------------------


def test_apply_creates_symlinks_from_yaml(tmp_path: Path) -> None:
    blobs = _make_blobs(tmp_path, ["qwen.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog(
        [("huggingface", "Qwen/Qwen-Image", "qwen.safetensors", blobs["qwen.safetensors"])]
    )
    applier.add(
        [{"source": "huggingface", "entry": "Qwen/Qwen-Image", "piece": "qwen.safetensors", "target_subdir": "checkpoints"}]
    )
    result = applier.apply(catalog)
    assert len(result.created) == 1
    assert len(result.errors) == 0
    sym = tmp_path / "vault" / "comfyui" / "checkpoints" / "qwen.safetensors"
    assert sym.is_symlink()
    assert sym.resolve() == blobs["qwen.safetensors"]


def test_apply_handles_missing_catalog_entry(tmp_path: Path) -> None:
    blobs = _make_blobs(tmp_path, ["foo.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([("huggingface", "Org/Repo", "foo.safetensors", blobs["foo.safetensors"])])
    applier.add(
        [{"source": "huggingface", "entry": "Org/NOT-THERE", "piece": "foo.safetensors", "target_subdir": "checkpoints"}]
    )
    result = applier.apply(catalog)
    assert len(result.errors) == 1
    assert "not found" in result.errors[0][1]
    assert not (tmp_path / "vault" / "comfyui" / "checkpoints" / "foo.safetensors").exists()


def test_apply_refuses_to_clobber_regular_file(tmp_path: Path) -> None:
    blobs = _make_blobs(tmp_path, ["foo.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([("huggingface", "Org/Repo", "foo.safetensors", blobs["foo.safetensors"])])
    applier.add(
        [{"source": "huggingface", "entry": "Org/Repo", "piece": "foo.safetensors", "target_subdir": "checkpoints"}]
    )
    target = tmp_path / "vault" / "comfyui" / "checkpoints" / "foo.safetensors"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"user data")
    result = applier.apply(catalog)
    assert any("not a symlink" in err for _, err in result.errors)
    assert target.read_bytes() == b"user data"


def test_apply_replaces_wrong_target_symlink(tmp_path: Path) -> None:
    blobs = _make_blobs(tmp_path, ["foo.safetensors", "bar.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([("huggingface", "Org/Repo", "foo.safetensors", blobs["foo.safetensors"])])
    applier.add(
        [{"source": "huggingface", "entry": "Org/Repo", "piece": "foo.safetensors", "target_subdir": "checkpoints"}]
    )
    target = tmp_path / "vault" / "comfyui" / "checkpoints" / "foo.safetensors"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(blobs["bar.safetensors"])

    result = applier.apply(catalog)
    assert len(result.updated) == 1
    assert target.resolve() == blobs["foo.safetensors"]


def test_apply_is_idempotent(tmp_path: Path) -> None:
    blobs = _make_blobs(tmp_path, ["foo.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([("huggingface", "Org/Repo", "foo.safetensors", blobs["foo.safetensors"])])
    applier.add(
        [{"source": "huggingface", "entry": "Org/Repo", "piece": "foo.safetensors", "target_subdir": "checkpoints"}]
    )
    first = applier.apply(catalog)
    second = applier.apply(catalog)
    assert len(first.created) == 1
    assert len(second.created) == 0
    assert len(second.updated) == 0


# --- prune_dangling --------------------------------------------------------


def test_prune_removes_dangling_symlinks(tmp_path: Path) -> None:
    blobs = _make_blobs(tmp_path, ["foo.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([("huggingface", "Org/Repo", "foo.safetensors", blobs["foo.safetensors"])])
    applier.add(
        [{"source": "huggingface", "entry": "Org/Repo", "piece": "foo.safetensors", "target_subdir": "checkpoints"}]
    )
    applier.apply(catalog)

    blobs["foo.safetensors"].unlink()

    result = applier.prune_dangling()
    assert len(result.removed) == 1
    target = tmp_path / "vault" / "comfyui" / "checkpoints" / "foo.safetensors"
    assert not target.exists()
    assert applier.list_current(catalog) == []


def test_prune_preserves_user_owned_symlinks(tmp_path: Path) -> None:
    """Symlinks not tracked by the yaml are not pruned (they may be user-managed)."""
    blobs = _make_blobs(tmp_path, ["foo.safetensors"])
    applier = _make_applier(tmp_path)
    target_dir = tmp_path / "vault" / "comfyui" / "user_owned"
    target_dir.mkdir(parents=True, exist_ok=True)
    user_sym = target_dir / "user_link.safetensors"
    user_sym.symlink_to(blobs["foo.safetensors"])
    result = applier.prune_dangling()
    assert result.removed == []
    assert user_sym.is_symlink()


def test_prune_handles_no_symlinks(tmp_path: Path) -> None:
    applier = _make_applier(tmp_path)
    result = applier.prune_dangling()
    assert result.removed == []


# --- list_current reflects yaml and disk ----------------------------------


def test_list_current_reports_dangling_via_symlink(tmp_path: Path) -> None:
    """``target_path`` reflects the catalog, not filesystem existence.

    A symlink is "dangling" when its target no longer exists on disk.
    The catalog itself doesn't track filesystem state, so
    ``target_path`` is whatever the catalog has stored. The
    ``prune_dangling`` step is the canonical place to detect and
    remove such symlinks.
    """
    blobs = _make_blobs(tmp_path, ["foo.safetensors"])
    applier = _make_applier(tmp_path)
    catalog = _make_catalog([("huggingface", "Org/Repo", "foo.safetensors", blobs["foo.safetensors"])])
    applier.add(
        [{"source": "huggingface", "entry": "Org/Repo", "piece": "foo.safetensors", "target_subdir": "checkpoints"}]
    )
    applier.apply(catalog)
    blobs["foo.safetensors"].unlink()

    rows = applier.list_current(catalog)
    assert len(rows) == 1
    # Catalog still has the piece's path stored; whether the file exists
    # is a filesystem question, not a catalog question.
    assert rows[0].target_path == blobs["foo.safetensors"]

    result = applier.prune_dangling()
    assert len(result.removed) == 1


# --- dataclass default factories ------------------------------------------


def test_apply_result_default_factory() -> None:
    r = ApplyResult()
    assert r.created == []
    assert r.updated == []
    assert r.errors == []


def test_prune_result_default_factory() -> None:
    r = PruneResult()
    assert r.removed == []
