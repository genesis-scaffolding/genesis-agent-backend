"""Tests for the LlamaSwapService surface: paths, stores, config generation.

Lifecycle behavior is exercised in :mod:`test_lifecycle` against a fake shim.
"""

from __future__ import annotations

from pathlib import Path

from genesis_worker import GenesisWorker
from genesis_worker.services.llama_swap import LlamaSwapService
from genesis_worker.services.llama_swap.generate_config import (
    is_config_stale,
    read_generated_at,
)
from genesis_worker.services.llama_swap.recipes import BUNDLED_RECIPES_PATH
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.tests._factories import service_ctx

# ---------------------------------------------------------------------------
# Paths the service owns
# ---------------------------------------------------------------------------


def test_config_path_defaults_under_the_service_data_dir(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    assert svc.config_path == tmp_path / "data" / "config.yaml"


def test_config_path_option_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.yaml"
    svc = LlamaSwapService(service_ctx(tmp_path, options={"config_path": explicit}))
    assert svc.config_path == explicit


def test_overrides_live_next_to_config(tmp_path: Path) -> None:
    cfg = tmp_path / "nested" / "my-config.yaml"
    svc = LlamaSwapService(service_ctx(tmp_path, options={"config_path": cfg}))
    assert svc.overrides_path == cfg.parent / "overrides.yaml"


def test_recipes_default_to_the_bundled_copy(tmp_path: Path) -> None:
    """Recipes ship with the plugin; they are not user configuration (ADR-009)."""
    svc = LlamaSwapService(service_ctx(tmp_path))
    assert svc.recipes_path == BUNDLED_RECIPES_PATH
    assert svc.recipes_path.is_file()
    assert svc.list_recipes().matchable


def test_recipes_path_option_wins(tmp_path: Path) -> None:
    custom = tmp_path / "recipes.yaml"
    custom.write_text("recipes:\n  default:\n    ctx_min: 1024\n")
    svc = LlamaSwapService(service_ctx(tmp_path, options={"recipes_path": custom}))
    assert svc.recipes_path == custom
    assert svc.list_recipes().default is not None


# ---------------------------------------------------------------------------
# config.yaml timestamps
# ---------------------------------------------------------------------------


def test_read_generated_at_returns_field(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("generated_at: '2026-08-10T00:00:00+00:00'\nmodels: {}\n")
    assert read_generated_at(cfg) == "2026-08-10T00:00:00+00:00"


def test_read_generated_at_recognizes_legacy_comment_header(tmp_path: Path) -> None:
    """``bin/build-config.py`` writes the timestamp as a comment, not a field."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "# llama-swap config generated 2026-08-10T00:00:00+00:00\n"
        "healthCheckTimeout: 60\n"
        "models: {}\n"
    )
    assert read_generated_at(cfg) == "2026-08-10T00:00:00+00:00"


def test_read_generated_at_prefers_yaml_field_over_comment(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "# llama-swap config generated 2026-08-10T00:00:00+00:00\n"
        "generated_at: '2026-08-11T00:00:00+00:00'\n"
        "models: {}\n"
    )
    assert read_generated_at(cfg) == "2026-08-11T00:00:00+00:00"


def test_read_generated_at_returns_none_when_field_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("healthCheckTimeout: 60\nmodels: {}\n")
    assert read_generated_at(cfg) is None


def test_read_generated_at_returns_none_when_missing_file(tmp_path: Path) -> None:
    assert read_generated_at(tmp_path / "absent.yaml") is None


def test_read_generated_at_returns_none_when_malformed(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(": not valid yaml :: :::\n")
    assert read_generated_at(cfg) is None


def test_is_config_stale_when_timestamp_differs(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("generated_at: 2026-08-10T00:00:00+00:00\nmodels: {}\n")
    assert is_config_stale(cfg, catalog_generated_at="2026-08-10T00:00:01+00:00") is True


def test_is_config_stale_false_when_timestamp_matches(tmp_path: Path) -> None:
    ts = "2026-08-10T00:00:00+00:00"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"generated_at: '{ts}'\nmodels: {{}}\n")
    assert is_config_stale(cfg, catalog_generated_at=ts) is False


def test_is_config_stale_true_when_field_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("models: {}\n")
    assert is_config_stale(cfg, catalog_generated_at="2026-08-10T00:00:00+00:00") is True


# ---------------------------------------------------------------------------
# regenerate_config
# ---------------------------------------------------------------------------


def test_regenerate_config_writes_generated_at_and_no_longer_stale(tmp_path: Path) -> None:
    """The service owns its paths and stores; the caller supplies only the catalog."""
    recipes = tmp_path / "recipes.yaml"
    recipes.write_text("recipes:\n  default:\n    match:\n    ctx_min: 1024\n")

    worker = GenesisWorker(Settings(paths=PathsSettings(vault_path=tmp_path)))
    catalog = worker.rescan_catalog()

    svc = LlamaSwapService(service_ctx(tmp_path, options={"recipes_path": recipes}))
    assert svc.regenerate_config(catalog) is True

    assert svc.config_path.is_file()
    embedded = read_generated_at(svc.config_path)
    assert embedded is not None
    assert embedded == catalog.generated_at
    assert svc.last_generated_at() == embedded
    assert is_config_stale(svc.config_path, catalog_generated_at=embedded) is False


def test_regenerate_config_is_a_no_op_when_nothing_changed(tmp_path: Path) -> None:
    recipes = tmp_path / "recipes.yaml"
    recipes.write_text("recipes:\n  default:\n    match:\n    ctx_min: 1024\n")
    worker = GenesisWorker(Settings(paths=PathsSettings(vault_path=tmp_path)))
    catalog = worker.rescan_catalog()

    svc = LlamaSwapService(service_ctx(tmp_path, options={"recipes_path": recipes}))
    assert svc.regenerate_config(catalog) is True
    assert svc.regenerate_config(catalog) is False


def test_last_generated_at_returns_none_when_config_absent(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    assert svc.last_generated_at() is None


def test_facade_regenerates_service_config(tmp_path: Path) -> None:
    """The framework drives generation through the contract, not the concrete class."""
    worker = GenesisWorker(
        Settings(
            paths=PathsSettings(vault_path=tmp_path, data_dir=tmp_path / "data"),
            services={"llama_swap": {"recipes_path": BUNDLED_RECIPES_PATH}},
        )
    )
    assert worker.regenerate_service_config("llama_swap") is True
    assert (tmp_path / "data" / "llama-swap" / "config.yaml").is_file()


# ---------------------------------------------------------------------------
# tail_log
# ---------------------------------------------------------------------------


def test_tail_log_returns_empty_when_file_missing(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    assert svc.tail_log() == ""


def test_tail_log_returns_full_content_when_smaller_than_limit(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    log = tmp_path / "log" / "llama-swap.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("hello\nworld\n")
    assert svc.tail_log(1024) == "hello\nworld\n"


def test_tail_log_returns_only_last_n_bytes(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    log = tmp_path / "log" / "llama-swap.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # 200 bytes of content; ask for 50.
    log.write_bytes(b"x" * 100 + b"y" * 100)
    tail = svc.tail_log(50)
    assert len(tail) == 50
    assert tail == "y" * 50


def test_tail_log_replaces_invalid_utf8(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    log = tmp_path / "log" / "llama-swap.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"ok \xff garbage\n")
    # No exception, replacement char somewhere in the output.
    tail = svc.tail_log()
    assert "ok " in tail
    assert "garbage" in tail


# ---------------------------------------------------------------------------
# evaluate_model_config / list_overrides / save_overrides_for_entry
# ---------------------------------------------------------------------------


def test_evaluate_model_config_returns_empty_for_empty_catalog(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    from genesis_worker.contracts.catalog import Catalog

    catalog = Catalog(
        root=str(tmp_path), generated_at="2026-01-01T00:00:00+00:00",
        huggingface=[], lmstudio=[],
    )
    assert svc.evaluate_model_config(catalog) == {}


def test_list_overrides_returns_empty_when_store_missing(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    assert svc.list_overrides() == {}


def test_save_overrides_for_entry_round_trips(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    svc.save_overrides_for_entry("foo", {"parallel": 5, "kv_cache": "q4_0"})
    assert svc.list_overrides() == {"foo": {"parallel": 5, "kv_cache": "q4_0"}}


def test_save_overrides_for_entry_clear_removes_only_that_entry(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    svc.save_overrides_for_entry("foo", {"parallel": 5})
    svc.save_overrides_for_entry("bar", {"kv_cache": "q4_0"})
    svc.save_overrides_for_entry("foo", {})
    assert svc.list_overrides() == {"bar": {"kv_cache": "q4_0"}}
