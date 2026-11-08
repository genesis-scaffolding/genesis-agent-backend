"""Tests for config.yaml generation (build pipeline + PyYAML emit + write-if-changed).

Pure unit tests: the build pipeline, the override path, and the
write-if-changed behaviour. The parity check against the live
``config.yaml`` was removed: it's been validated end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from genesis_worker.contracts.catalog import Catalog, ModelEntry, ModelPiece
from genesis_worker.services.llama_swap.generate_config import (
    BuildOptions,
    build_config,
    emit_payload,
    make_display_name,
    short_source_label,
    write_config,
)
from genesis_worker.services.llama_swap.radiance import RadianceSettings
from genesis_worker.services.llama_swap.recipes import Recipe, Recipes


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


# ---------------------------------------------------------------------------
# Radiance entries
# ---------------------------------------------------------------------------


def _radiance_catalog() -> Catalog:
    return Catalog(
        root="/tmp/vault",
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash="x",
        entries=[
            ModelEntry(
                name="StillDeadcode/qwen3.8-27b-mxfp4",
                source="huggingface",
                pieces=[
                    ModelPiece(
                        role="main",
                        filename="qwen3.8-27b-mxfp4.rad",
                        path=Path("/tmp/vault/models--StillDeadcode--qwen3.8-27b-mxfp4/blobs/sha"),
                        bytes=19_000_000_000,
                    )
                ],
                total_bytes=19_000_000_000,
                directory="/tmp/vault/models--StillDeadcode--qwen3.8-27b-mxfp4",
                notes=[],
                extra={},
            )
        ],
    )


def _radiance_entries(tmp_path: Path) -> list[tuple[str, dict]]:
    options = BuildOptions(
        repo_root=tmp_path,
        radiance=RadianceSettings(
            image_ref="stilldeadcode/radiance:latest",
            group_gids=(("render", "992"), ("video", "44")),
        ),
    )
    recipes = Recipes(
        default=Recipe(name="default"),
        matchable=[Recipe(name="radiance-qwen3-8-27b-mxfp4", match="qwen3.8-27b-mxfp4")],
    )
    return build_config(_radiance_catalog(), recipes, options=options)


def test_rad_entry_emits_cmd_stop_and_check_endpoint(tmp_path: Path) -> None:
    entries = _radiance_entries(tmp_path)
    payload = emit_payload(entries, root="/tmp", generated_at="x")
    model = payload["models"]["qwen3-8-27b-mxfp4"]
    assert model["cmdStop"] == "docker stop --time 60 ${MODEL_ID}"
    assert model["checkEndpoint"] == "/v1/models"
    assert model["proxy"] == "http://127.0.0.1:${PORT}"


def test_rad_entry_emits_unload_timeout_longer_than_the_stop_grace(tmp_path: Path) -> None:
    entries = _radiance_entries(tmp_path)
    payload = emit_payload(entries, root="/tmp", generated_at="x")
    model = payload["models"]["qwen3-8-27b-mxfp4"]
    assert model["unloadTimeout"] > 60


def test_rad_entry_emits_no_env_array(tmp_path: Path) -> None:
    entries = _radiance_entries(tmp_path)
    payload = emit_payload(entries, root="/tmp", generated_at="x")
    model = payload["models"]["qwen3-8-27b-mxfp4"]
    assert "env" not in model


def test_emitted_global_health_check_timeout_follows_the_option() -> None:
    payload = emit_payload(
        [],
        root="/tmp",
        generated_at="x",
        health_check_timeout_s=777,
    )
    assert payload["healthCheckTimeout"] == 777


def test_llama_server_entry_payload_is_unchanged(tmp_path: Path) -> None:
    catalog = Catalog(
        root="/tmp/vault",
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash="x",
        entries=[
            ModelEntry(
                name="org/test",
                source="huggingface",
                pieces=[
                    ModelPiece(
                        role="main",
                        filename="test.gguf",
                        path=Path("/tmp/vault/test.gguf"),
                        bytes=1,
                    )
                ],
                total_bytes=1,
                directory="/tmp/vault/test",
                notes=[],
                extra={},
            )
        ],
    )
    entries = build_config(
        catalog,
        Recipes(default=Recipe(name="default"), matchable=[]),
        options=BuildOptions(repo_root=tmp_path),
    )
    payload = emit_payload(entries, root="/tmp", generated_at="x")
    model = payload["models"]["test"]
    assert model["proxy"] == "http://127.0.0.1:${PORT}"
    assert model["ttl"] == 0
    assert "cmdStop" not in model
    assert "checkEndpoint" not in model
    assert "unloadTimeout" not in model
    assert "env" not in model
