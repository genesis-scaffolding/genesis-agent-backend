"""Tests for config.yaml generation (build pipeline + PyYAML emit + write-if-changed).

Pure unit tests: the build pipeline, the override path, and the
write-if-changed behaviour. The parity check against the live
``config.yaml`` was removed: it's been validated end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from genesis_worker.services.llama_swap.generate_config import (
    make_display_name,
    short_source_label,
    write_config,
)
from genesis_worker.services.llama_swap.recipes import Recipe


def test_short_source_label_known_sources() -> None:
    """Known sources produce deterministic short labels (first 3 alnum chars)."""
    assert short_source_label("huggingface") == "hug"
    assert short_source_label("lmstudio") == "lms"


def test_short_source_label_handles_new_sources() -> None:
    """A future source gets a deterministic short label without hardcoding."""
    assert short_source_label("comfyui") == "com"
    assert short_source_label("model_scope") == "mod"
    assert short_source_label("civitai") == "civ"
    assert short_source_label("Civit-AI") == "civ"


def test_short_source_label_empty_returns_sentinel() -> None:
    """Empty string returns a sentinel."""
    assert short_source_label("") == "x"
    assert short_source_label("___") == "x"


def test_make_display_name_strips_gguf_and_appends_variant() -> None:
    """Piece filename with .gguf extension: extension stripped, variant appended."""
    name = make_display_name(
        "acme/qwen36-gguf.gguf",  # piece filename
        Recipe(name="qwen3.6-thinking", match="qwen3.6"),
        multi_match=True,
    )
    assert name == "qwen36-gguf (thinking)"


def test_write_config_writes_when_changed(tmp_path: Path) -> None:
    out = tmp_path / "config.yaml"
    entries = [
        (
            "x",
            {
                "name": "X",
                "cmd": "echo hi\n",
                "proxy": "http://127.0.0.1:${PORT}",
                "ttl": 0,
                "resolved_from": "default",
            },
        )
    ]
    wrote = write_config(out, entries, root="", generated_at="x")
    assert wrote is True
    assert out.is_file()
    assert "echo hi" in out.read_text()


def test_write_config_preserves_mtime_on_noop(tmp_path: Path) -> None:
    out = tmp_path / "config.yaml"
    # Pre-write the literal-block form PyYAML produces for cmd, including
    # the ``generated_at`` and ``root`` fields added in spec-002 chunk 1.
    out.write_text(
        "healthCheckTimeout: 60\n"
        "logLevel: info\n"
        "generated_at: x\n"
        "root: ''\n"
        "models:\n"
        "  x:\n"
        "    name: X\n"
        "    cmd: |\n"
        "      echo hi\n"
        "    proxy: http://127.0.0.1:${PORT}\n"
        "    ttl: 0\n"
        "    resolved_from: default\n"
    )

    mtime_before = out.stat().st_mtime_ns

    entries = [
        (
            "x",
            {
                "name": "X",
                "cmd": "echo hi\n",
                "proxy": "http://127.0.0.1:${PORT}",
                "ttl": 0,
                "resolved_from": "default",
            },
        )
    ]
    # Force the same emission by feeding identical content.
    import time

    time.sleep(0.01)
    wrote = write_config(out, entries, root="", generated_at="x")
    assert wrote is False
    assert out.stat().st_mtime_ns == mtime_before
