"""Tests for the unified catalog service."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.catalog.build import CatalogService


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


def test_rescan_merges_hf_and_lms(fake_vault: Path) -> None:
    cat = CatalogService(fake_vault).rescan()
    assert len(cat.huggingface) == 1
    assert len(cat.lmstudio) == 1
    assert cat.huggingface[0].name == "acme/demo"
    assert cat.lmstudio[0].name == "acme/demo-lm"
    assert cat.root == str(fake_vault)


def test_rescan_populates_total_bytes(fake_vault: Path) -> None:
    cat = CatalogService(fake_vault).rescan()
    assert cat.huggingface[0].total_bytes == 1024
    assert cat.lmstudio[0].total_bytes == 512


def test_rescan_handles_empty_vault(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path).rescan()
    assert cat.huggingface == []
    assert cat.lmstudio == []


def test_rescan_records_source_label(fake_vault: Path) -> None:
    cat = CatalogService(fake_vault).rescan()
    assert cat.huggingface[0].source == "huggingface"
    assert cat.lmstudio[0].source == "lmstudio"
