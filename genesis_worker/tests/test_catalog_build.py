"""Tests for the unified catalog service."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.catalog_build import CatalogService
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.sources import SourceRegistry


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
    assert len(cat.huggingface) == 1
    assert len(cat.lmstudio) == 1
    assert cat.huggingface[0].name == "acme/demo"
    assert cat.lmstudio[0].name == "acme/demo-lm"
    assert cat.root == str(fake_vault)


def test_rescan_populates_total_bytes(registry_for: SourceRegistry) -> None:
    cat = CatalogService(registry_for).rescan()
    assert cat.huggingface[0].total_bytes == 1024
    assert cat.lmstudio[0].total_bytes == 512


def test_rescan_handles_empty_vault(tmp_path: Path) -> None:
    registry = SourceRegistry(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = CatalogService(registry).rescan()
    assert cat.huggingface == []
    assert cat.lmstudio == []


def test_rescan_records_source_label(registry_for: SourceRegistry) -> None:
    cat = CatalogService(registry_for).rescan()
    assert cat.huggingface[0].source == "huggingface"
    assert cat.lmstudio[0].source == "lmstudio"


# ---------------------------------------------------------------------------
# Catalog.by_source() — source-agnostic iteration
# ---------------------------------------------------------------------------


def test_by_source_groups_entries(tmp_path: Path) -> None:
    """``by_source()`` returns ``{field_name: [entries]}`` for every source field."""
    registry = SourceRegistry(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = CatalogService(registry).rescan()
    grouped = cat.by_source()
    assert set(grouped) == {"huggingface", "lmstudio"}
    # Same object identity — accessor returns the field directly.
    assert grouped["huggingface"] is cat.huggingface
    assert grouped["lmstudio"] is cat.lmstudio


def test_by_source_works_for_empty_catalog(tmp_path: Path) -> None:
    """An empty catalog returns ``{huggingface: [], lmstudio: []}``."""
    registry = SourceRegistry(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = CatalogService(registry).rescan()
    assert cat.by_source() == {"huggingface": [], "lmstudio": []}


def test_by_source_after_population(registry_for: SourceRegistry) -> None:
    """After rescan, ``by_source()`` returns the populated per-source lists."""
    cat = CatalogService(registry_for).rescan()
    grouped = cat.by_source()
    assert len(grouped["huggingface"]) == 1
    assert len(grouped["lmstudio"]) == 1
    assert grouped["huggingface"][0].name == "acme/demo"
    assert grouped["lmstudio"][0].name == "acme/demo-lm"
