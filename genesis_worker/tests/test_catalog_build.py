"""Tests for the unified catalog service."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from genesis_worker.catalog_build import CatalogService, compute_content_hash
from genesis_worker.contracts import ModelEntry, ModelPiece
from genesis_worker.registries import SourceRegistry
from genesis_worker.settings import PathsSettings, Settings


@pytest.fixture
def fake_vault(tmp_path: Path) -> Path:
    """A vault with one HF repo and one LMS model."""
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--demo"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.gguf").write_bytes(b"\x00" * 1024)
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc")

    models = tmp_path / "lmstudio" / "models"
    lm = models / "acme" / "demo-lm"
    lm.mkdir(parents=True)
    (lm / "model.gguf").write_bytes(b"\x00" * 512)
    return tmp_path


@pytest.fixture
def registry_for(fake_vault: Path) -> SourceRegistry:
    """A SourceRegistry pointed at the fake vault via settings."""
    return SourceRegistry(Settings(paths=PathsSettings(vault_path=fake_vault)))


def test_rescan_merges_hf_and_lms(registry_for: SourceRegistry, fake_vault: Path) -> None:
    cat = CatalogService(registry_for).rescan()
    grouped = cat.by_source()
    assert len(grouped["huggingface"]) == 1
    assert len(grouped["lmstudio"]) == 1
    assert grouped["huggingface"][0].name == "acme/demo"
    assert grouped["lmstudio"][0].name == "acme/demo-lm"
    assert cat.root == str(fake_vault)


def test_rescan_populates_total_bytes(registry_for: SourceRegistry) -> None:
    cat = CatalogService(registry_for).rescan()
    grouped = cat.by_source()
    assert grouped["huggingface"][0].total_bytes == 1024
    assert grouped["lmstudio"][0].total_bytes == 512


def test_rescan_handles_empty_vault(tmp_path: Path) -> None:
    registry = SourceRegistry(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = CatalogService(registry).rescan()
    assert cat.by_source() == {}
    assert cat.entries == []


def test_rescan_records_source_label(registry_for: SourceRegistry) -> None:
    cat = CatalogService(registry_for).rescan()
    grouped = cat.by_source()
    assert grouped["huggingface"][0].source == "huggingface"
    assert grouped["lmstudio"][0].source == "lmstudio"


# ---------------------------------------------------------------------------
# Catalog.by_source() — source-agnostic iteration
# ---------------------------------------------------------------------------


def test_by_source_groups_entries(tmp_path: Path) -> None:
    """``by_source()`` groups entries by their ``source`` field."""
    registry = SourceRegistry(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = CatalogService(registry).rescan()
    grouped = cat.by_source()
    # Empty vault → no entries → no groups.
    assert grouped == {}


def test_by_source_works_for_empty_catalog(tmp_path: Path) -> None:
    """An empty catalog returns an empty grouping."""
    registry = SourceRegistry(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = CatalogService(registry).rescan()
    assert cat.by_source() == {}


def test_by_source_after_population(registry_for: SourceRegistry) -> None:
    """After rescan, ``by_source()`` returns the populated per-source lists."""
    cat = CatalogService(registry_for).rescan()
    grouped = cat.by_source()
    assert len(grouped["huggingface"]) == 1
    assert len(grouped["lmstudio"]) == 1
    assert grouped["huggingface"][0].name == "acme/demo"
    assert grouped["lmstudio"][0].name == "acme/demo-lm"


# ---------------------------------------------------------------------------
# compute_content_hash — stable identity for the catalog
# ---------------------------------------------------------------------------


def _entry(name: str, source: str, total_bytes: int, pieces: list[ModelPiece]) -> ModelEntry:
    return ModelEntry(
        name=name,
        source=source,
        pieces=pieces,
        total_bytes=total_bytes,
        directory="",
    )


def _piece(role: str, filename: str, n: int) -> ModelPiece:
    return ModelPiece(role=role, filename=filename, path=Path("/x"), bytes=n)


def test_compute_content_hash_is_stable_across_calls() -> None:
    """Same input → same hash. Pure function."""
    entries = [_entry("a/b", "huggingface", 100, [_piece("main", "x.gguf", 100)])]
    assert compute_content_hash(entries) == compute_content_hash(entries)


def test_compute_content_hash_is_stable_under_reordering() -> None:
    """Entry order doesn't matter — sort is by (source, name)."""
    a = _entry("a/b", "huggingface", 100, [_piece("main", "x.gguf", 100)])
    b = _entry("c/d", "lmstudio", 200, [_piece("main", "y.gguf", 200)])
    assert compute_content_hash([a, b]) == compute_content_hash([b, a])


def test_compute_content_hash_changes_when_piece_bytes_change() -> None:
    e1 = _entry("a/b", "huggingface", 100, [_piece("main", "x.gguf", 100)])
    e2 = _entry("a/b", "huggingface", 200, [_piece("main", "x.gguf", 200)])
    assert compute_content_hash([e1]) != compute_content_hash([e2])


def test_compute_content_hash_changes_when_piece_filename_changes() -> None:
    e1 = _entry("a/b", "huggingface", 100, [_piece("main", "x.gguf", 100)])
    e2 = _entry("a/b", "huggingface", 100, [_piece("main", "y.gguf", 100)])
    assert compute_content_hash([e1]) != compute_content_hash([e2])


def test_compute_content_hash_empty_is_stable() -> None:
    """Empty entries → known-stable hash."""
    expected = hashlib.sha256(b"[]").hexdigest()
    assert compute_content_hash([]) == expected
    assert compute_content_hash([]) == compute_content_hash([])


def test_compute_content_hash_ignores_directory_notes_extra(tmp_path: Path) -> None:
    """directory/notes/extra are not part of the hash."""
    p = _piece("main", "x.gguf", 100)
    base = ModelEntry(
        name="a/b", source="huggingface", pieces=[p],
        total_bytes=100, directory="/old/path", notes=[], extra={},
    )
    same = ModelEntry(
        name="a/b", source="huggingface", pieces=[p],
        total_bytes=100, directory="/totally/different", notes=["x"], extra={"k": "v"},
    )
    assert compute_content_hash([base]) == compute_content_hash([same])
