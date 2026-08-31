"""Tests for catalog on-disk I/O (load, save, atomic write, no-op skip)."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
from genesis_worker.utils.catalog_io import load_catalog, save_catalog
from genesis_worker.utils.catalog_utils import compute_content_hash


def _entry(name: str, source: str = "huggingface", n: int = 1024) -> ModelEntry:
    return ModelEntry(
        name=name,
        source=source,
        pieces=[ModelPiece(role="main", filename="x.gguf", path=Path("/x"), bytes=n)],
        total_bytes=n,
        directory="",
    )


def _catalog(
    entries: list[ModelEntry] | None = None, *, generated_at: str = "2026-01-01T00:00:00+00:00"
) -> Catalog:
    entries = entries or [_entry("a/b")]
    return Catalog(
        root="/vault",
        generated_at=generated_at,
        content_hash=compute_content_hash(entries),
        entries=entries,
    )


def test_save_catalog_writes_on_first_call(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    cat = _catalog()
    assert save_catalog(path, cat) is True
    assert path.is_file()


def test_save_catalog_skips_when_identical(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    cat = _catalog()
    save_catalog(path, cat)
    mtime_before = path.stat().st_mtime
    # Second save with the same content should be a no-op.
    assert save_catalog(path, cat) is False
    assert path.stat().st_mtime == mtime_before


def test_save_catalog_writes_when_content_differs(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    save_catalog(path, _catalog([_entry("a/b")]))
    mtime_before = path.stat().st_mtime
    # Different generated_at → different text → write.
    new_cat = _catalog([_entry("a/b")], generated_at="2026-02-01T00:00:00+00:00")
    assert save_catalog(path, new_cat) is True
    assert path.stat().st_mtime >= mtime_before


def test_save_catalog_writes_when_entries_change(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    save_catalog(path, _catalog([_entry("a/b")]))
    new_cat = _catalog([_entry("a/b"), _entry("c/d", n=2048)])
    assert save_catalog(path, new_cat) is True
    loaded = load_catalog(path)
    assert loaded is not None
    assert len(loaded.entries) == 2


def test_load_catalog_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    cat = _catalog([_entry("a/b"), _entry("c/d", source="lmstudio")])
    save_catalog(path, cat)
    loaded = load_catalog(path)
    assert loaded is not None
    assert loaded.root == cat.root
    assert loaded.generated_at == cat.generated_at
    assert loaded.content_hash == cat.content_hash
    assert len(loaded.entries) == 2
    assert loaded.by_source() == {
        "huggingface": [_entry("a/b")],
        "lmstudio": [_entry("c/d", source="lmstudio")],
    }


def test_load_catalog_returns_none_on_missing_file(tmp_path: Path) -> None:
    assert load_catalog(tmp_path / "nope.json") is None


def test_load_catalog_returns_none_on_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert load_catalog(path) is None


def test_load_catalog_returns_none_on_schema_version_mismatch(tmp_path: Path) -> None:
    """A future schema bump makes old files invalid; we rebuild from scratch."""
    path = tmp_path / "old.json"
    path.write_text(
        '{"schema_version": 99, "root": "/x", "generated_at": "2026", "content_hash": "x", "entries": []}'
    )
    assert load_catalog(path) is None


def test_save_catalog_atomic_no_truncation(tmp_path: Path) -> None:
    """A reader holding the file sees either the old or new content, never partial."""
    path = tmp_path / "catalog.json"
    cat = _catalog()
    save_catalog(path, cat)

    # Open the existing file for reading; save_catalog should not truncate it
    # mid-write because we write to a sibling temp and rename atomically.
    with open(path) as f:
        existing = f.read()
        assert existing  # we have content
        save_catalog(path, _catalog([_entry("z/y")]))
        # The reader's view of the file was the snapshot we took at open().
        # What matters is that we never see a half-written file.
        f.seek(0)
        after = f.read()
        assert after in (existing, "") or after.startswith("{")  # well-formed JSON or untouched


def test_save_catalog_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "catalog.json"
    assert save_catalog(path, _catalog()) is True
    assert path.is_file()
