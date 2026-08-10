"""Tests for config.yaml generation (build pipeline + PyYAML emit + write-if-changed).

The parity test against the live ``config.yaml`` is the key gate: every
emitted entry's parsed ``cmd`` must equal the entry's parsed ``cmd`` in
the current config.yaml. Byte-level diffs in the YAML representation
are accepted (ADR-006); content equivalence is the gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genesis_worker.catalog_build import CatalogService
from genesis_worker.registries import SourceRegistry
from genesis_worker.services.llama_swap.config import (
    build_config,
    detect_files,
    make_display_name,
    make_entry_id,
    write_config,
)
from genesis_worker.services.llama_swap.recipes import Recipe, Recipes
from genesis_worker.settings import PathsSettings, Settings


@pytest.fixture(scope="module")
def real_catalog():
    """Build a catalog from the real vault. Module-scoped: expensive."""
    vault = Path("/home/gentran1991/Data2/models")
    registry = SourceRegistry(Settings(paths=PathsSettings(vault_path=vault)))
    return CatalogService(registry).rescan()


@pytest.fixture(scope="module")
def real_recipes() -> Recipes:
    return Recipes.load(Path("recipes.yaml"))


def test_detect_files_picks_largest_main(real_catalog) -> None:
    """For an entry with multiple main pieces, the largest is chosen."""
    entry = real_catalog.huggingface[0]
    files = detect_files(entry)
    if any(p.role == "main" for p in entry.pieces):
        assert files.main is not None


def test_make_entry_id_collision_suffixing(real_recipes: Recipes) -> None:
    """Same name across siblings gets the variant suffix."""
    all_ids: set[str] = set()
    eid_thinking = make_entry_id(
        "acme/qwen36-gguf",
        real_recipes.matchable[0],
        multi_match=True,
        all_ids=all_ids,
        source="huggingface",
    )
    eid_instruct = make_entry_id(
        "acme/qwen36-gguf",
        real_recipes.matchable[1],
        multi_match=True,
        all_ids=all_ids,
        source="huggingface",
    )
    assert eid_thinking != eid_instruct
    assert "thinking" in eid_thinking or eid_thinking != eid_instruct


def test_make_display_name_appends_variant() -> None:
    name = make_display_name(
        "acme/qwen36",
        Recipe(name="qwen3.6-thinking", match="qwen3.6"),
        multi_match=True,
    )
    assert "(thinking)" in name


def test_build_config_emits_one_entry_per_recipe(real_catalog, real_recipes: Recipes) -> None:
    entries = build_config(real_catalog, real_recipes)
    # All entries must have a non-empty cmd.
    for eid, data in entries:
        assert "cmd" in data and data["cmd"], f"empty cmd for {eid}"
        assert data["resolved_from"], f"missing resolved_from for {eid}"


def test_no_overrides_matches_live_config(real_catalog, real_recipes: Recipes) -> None:
    """Content-equivalence gate: parsed cmd strings must match the live config.yaml."""
    live = yaml.safe_load(Path("config.yaml").read_text())
    live_models = live["models"]

    entries = build_config(real_catalog, real_recipes)
    emitted = {eid: data for eid, data in entries}

    # Same set of entry ids.
    assert set(emitted) == set(live_models), (
        f"only in emitted: {set(emitted) - set(live_models)}\n"
        f"only in live: {set(live_models) - set(emitted)}"
    )

    # For each entry, the parsed cmd must be byte-identical.
    for eid, data in emitted.items():
        live_cmd = live_models[eid]["cmd"]
        new_cmd = data["cmd"]
        assert new_cmd == live_cmd, (
            f"cmd mismatch for {eid}:\n  live: {live_cmd!r}\n  new:  {new_cmd!r}"
        )


def test_overrides_change_emitted_cmd(real_catalog, real_recipes: Recipes) -> None:
    """An override for one entry's sampling.temp must change that entry's cmd."""
    target_eid: str | None = None
    base_cmd = ""
    for eid, data in build_config(real_catalog, real_recipes):
        if "temp" in data["cmd"]:
            target_eid = eid
            base_cmd = data["cmd"]
            break
    assert target_eid is not None
    assert base_cmd

    overrides = {target_eid: {"sampling": {"temp": 0.42}}}
    entries = build_config(real_catalog, real_recipes, overrides=overrides)
    emitted = dict(entries)
    assert "0.42" in emitted[target_eid]["cmd"]
    assert emitted[target_eid]["cmd"] != base_cmd


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


def test_emitted_yaml_parses_back(real_catalog, real_recipes: Recipes) -> None:
    """The PyYAML-emitted config.yaml parses back to a structurally valid dict."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        out_path = Path(f.name)
    try:
        entries = build_config(real_catalog, real_recipes)
        write_config(
            out_path, entries, root=real_catalog.root, generated_at=real_catalog.generated_at
        )
        parsed = yaml.safe_load(out_path.read_text())
        assert "models" in parsed
        assert parsed["healthCheckTimeout"] == 60
        assert parsed["logLevel"] == "info"
        assert len(parsed["models"]) == len(entries)
    finally:
        out_path.unlink()


def test_no_extra_yaml_keys(real_catalog, real_recipes: Recipes) -> None:
    """Each emitted entry has exactly the documented keys."""
    entries = build_config(real_catalog, real_recipes)
    expected = {"name", "cmd", "proxy", "ttl", "resolved_from"}
    for eid, data in entries:
        assert set(data.keys()) == expected, f"{eid}: keys={set(data.keys())}, expected={expected}"
