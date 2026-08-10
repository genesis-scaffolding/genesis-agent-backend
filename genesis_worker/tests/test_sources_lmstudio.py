"""Tests for the LM Studio walker."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.sources.lmstudio import LMSource


def _build_layout(tmp_path: Path) -> Path:
    """Build a minimal LM Studio layout: <tmp>/lmstudio/models/<publisher>/<model>/."""
    models_dir = tmp_path / "lmstudio" / "models"
    model_dir = models_dir / "acme" / "demo-model"
    model_dir.mkdir(parents=True)
    (model_dir / "demo-model-Q4.gguf").write_bytes(b"\x00" * 2048)
    (model_dir / "demo-model-mmproj.gguf").write_bytes(b"\x00" * 256)
    (model_dir / "chat-template.jinja").write_text("hello")
    return models_dir


def test_walk_returns_one_entry_per_model_dir(tmp_path: Path) -> None:
    models_dir = _build_layout(tmp_path)
    src = LMSource(local_path=models_dir)
    models = src.walk()
    assert len(models) == 1
    m = models[0]
    assert m.source == "lmstudio"
    assert m.native_id == "acme/demo-model"
    # total_bytes sums ALL files in the model dir (weights + config + chat template).
    assert m.total_bytes == 2048 + 256 + len("hello")
    assert m.extra["publisher"] == "acme"


def test_walk_records_partial_download(tmp_path: Path) -> None:
    models_dir = tmp_path / "lmstudio" / "models"
    model_dir = models_dir / "acme" / "demo-model"
    model_dir.mkdir(parents=True)
    (model_dir / "model.Q4.gguf").write_bytes(b"\x00" * 100)
    (model_dir / "model.Q4.gguf.part").write_bytes(b"\x00" * 50)
    src = LMSource(local_path=models_dir)
    m = src.walk()[0]
    assert any("partial download" in n for n in m.notes)


def test_walk_notes_when_no_weights(tmp_path: Path) -> None:
    models_dir = tmp_path / "lmstudio" / "models"
    model_dir = models_dir / "acme" / "demo-model"
    model_dir.mkdir(parents=True)
    (model_dir / "chat-template.jinja").write_text("hi")
    src = LMSource(local_path=models_dir)
    m = src.walk()[0]
    assert "no model weights on disk" in m.notes


def test_walk_returns_empty_when_no_models_dir(tmp_path: Path) -> None:
    src = LMSource(local_path=tmp_path / "nope")
    assert src.walk() == []
    assert src.is_available() is False
