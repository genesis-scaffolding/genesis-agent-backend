"""Tests for the HuggingFace cache walker."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.sources.huggingface import HuggingFaceSource


@pytest.fixture
def fake_hub(tmp_path: Path) -> Path:
    """Build a minimal HF cache layout under tmp_path.

    Layout:
        <tmp>/huggingface/hub/
            models--acme--demo/
                refs/main                  (contains a sha)
                snapshots/<sha>/
                    model-Q4.gguf
                    mmproj-Q8.gguf
                    config.json
    """
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--demo"
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "model-Q4.gguf").write_bytes(b"\x00" * 1024)
    (snapshot / "mmproj-Q8.gguf").write_bytes(b"\x00" * 512)
    (snapshot / "config.json").write_text("{}")
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc123")
    return hub


def test_walk_returns_one_entry_per_repo(fake_hub: Path) -> None:
    src = HuggingFaceSource(local_path=fake_hub)
    models = src.walk()
    assert len(models) == 1
    m = models[0]
    assert m.source == "huggingface"
    assert m.native_id == "acme/demo"
    # total_bytes sums ALL files (weights + configs).
    assert m.total_bytes == 1024 + 512 + len("{}")
    assert m.extra["snapshot"] == "abc123"


def test_walk_classifies_pieces(fake_hub: Path) -> None:
    src = HuggingFaceSource(local_path=fake_hub)
    m = src.walk()[0]
    roles = {p.role for p in m.pieces}
    assert "main" in roles
    assert "mmproj" in roles
    assert "config" in roles


def test_walk_returns_empty_when_no_hub(tmp_path: Path) -> None:
    src = HuggingFaceSource(local_path=tmp_path / "nope")
    assert src.walk() == []
    assert src.is_available() is False


def test_walk_skips_partial_repo_without_refs(tmp_path: Path) -> None:
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--broken"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.gguf").write_bytes(b"\x00")
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    # no refs/main
    src = HuggingFaceSource(local_path=hub)
    assert src.walk() == []


def test_walk_notes_when_no_weights(tmp_path: Path) -> None:
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--config-only"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc")
    src = HuggingFaceSource(local_path=hub)
    m = src.walk()[0]
    assert "no model weights on disk" in m.notes
