"""Tests for genesis_worker.settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker import settings as settings_module
from genesis_worker.settings import (
    PathsSettings,
    Settings,
)


def test_settings_default_paths_use_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env vars, all path fields land under ~/.local/share|config|cache|state/genesis-worker."""
    for var in (
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "GENESIS_PATHS__DATA_DIR",
        "GENESIS_PATHS__CONFIG_DIR",
        "GENESIS_PATHS__CACHE_DIR",
        "GENESIS_PATHS__STATE_DIR",
        "GENESIS_PATHS__LOG_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    s = Settings()
    assert s.paths.data_dir == Path.home() / ".local/share" / "genesis-worker"
    assert s.paths.config_dir == Path.home() / ".config" / "genesis-worker"
    assert s.paths.cache_dir == Path.home() / ".cache" / "genesis-worker"
    assert s.paths.state_dir == Path.home() / ".local/state" / "genesis-worker"
    assert s.paths.log_dir == Path.home() / ".local/state" / "genesis-worker"


def test_settings_paths_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENESIS_PATHS__DATA_DIR", "/custom/data")
    s = Settings()
    assert s.paths.data_dir == Path("/custom/data")


def test_settings_xdg_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    p = PathsSettings()
    assert p.data_dir == Path("/xdg/data/genesis-worker")


def test_settings_resolved_vault_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GENESIS_PATHS__VAULT_PATH", raising=False)
    monkeypatch.delenv("MODELS_ROOT", raising=False)
    monkeypatch.setattr("genesis_worker.settings._read_models_root", lambda: None)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.paths.resolved_vault_path == Path.home() / "models"


def test_settings_resolved_vault_path_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENESIS_PATHS__VAULT_PATH", "/srv/vault")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.paths.resolved_vault_path == Path("/srv/vault")


def test_settings_resolved_vault_path_models_root_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MODELS_ROOT from os.environ is a backward-compat synonym for vault_path."""
    monkeypatch.delenv("GENESIS_PATHS__VAULT_PATH", raising=False)
    monkeypatch.delenv("MODELS_ROOT", raising=False)
    monkeypatch.setattr("genesis_worker.settings._read_models_root", lambda: "/srv/models")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.paths.resolved_vault_path == Path("/srv/models")


def test_settings_explicit_vault_path_wins_over_models_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENESIS_PATHS__VAULT_PATH", "/srv/vault")
    monkeypatch.setattr("genesis_worker.settings._read_models_root", lambda: "/srv/models")
    s = Settings()  # type: ignore[call-arg]
    assert s.paths.resolved_vault_path == Path("/srv/vault")


def test_settings_unknown_env_var_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENESIS_BOGUS", "junk")
    s = Settings()  # must not raise
    assert s is not None


def test_xdg_base_is_not_a_settings_field() -> None:
    """Renaming the directory is a code decision, not a user-settable option."""
    assert "XDG_BASE" not in PathsSettings.model_fields
    assert settings_module.XDG_BASE == "genesis-worker"


def test_xdg_base_drives_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The constant is load-bearing: change it and every directory follows."""
    monkeypatch.setattr(settings_module, "XDG_BASE", "renamed")
    paths = PathsSettings()
    assert {
        p.name
        for p in (paths.data_dir, paths.config_dir, paths.cache_dir, paths.state_dir, paths.log_dir)
    } == {"renamed"}


def test_plugin_option_slices_are_opaque_to_the_framework() -> None:
    """Settings carries slices; it does not know what keys a plugin accepts (ADR-009)."""
    s = Settings(services={"llama_swap": {"listen_addr": "0.0.0.0:1234"}})
    assert s.options_for("services", "llama_swap") == {"listen_addr": "0.0.0.0:1234"}
    assert s.options_for("services", "not_installed") == {}
    assert s.options_for("sources", "huggingface") == {}


def test_plugin_defaults_come_from_the_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    from genesis_worker.services.llama_swap.options import LlamaSwapOptions
    from genesis_worker.sources.huggingface.options import HuggingFaceOptions

    assert HuggingFaceOptions().default_revision == "main"
    assert LlamaSwapOptions().listen_addr == "0.0.0.0:8080"
    assert LlamaSwapOptions().kv_quant_over_bytes == 25_000_000_000


def test_nested_env_var_reaches_the_option_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENESIS_SERVICES", '{"llama_swap": {"listen_addr": "0.0.0.0:9999"}}')
    s = Settings()
    assert s.options_for("services", "llama_swap")["listen_addr"] == "0.0.0.0:9999"


def test_dev_env_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dev.env in cwd is picked up by pydantic-settings."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dev.env").write_text("GENESIS_PATHS__DATA_DIR=/tmp/dev-data\n")
    s = Settings()
    assert s.paths.data_dir == Path("/tmp/dev-data")


def test_dot_env_overrides_dev_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dev.env").write_text("GENESIS_PATHS__DATA_DIR=/tmp/dev-data\n")
    (tmp_path / ".env").write_text("GENESIS_PATHS__DATA_DIR=/tmp/dotenv-data\n")
    s = Settings()
    assert s.paths.data_dir == Path("/tmp/dotenv-data")


def test_real_env_var_overrides_dot_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dev.env").write_text("GENESIS_PATHS__DATA_DIR=/tmp/dev-data\n")
    (tmp_path / ".env").write_text("GENESIS_PATHS__DATA_DIR=/tmp/dotenv-data\n")
    monkeypatch.setenv("GENESIS_PATHS__DATA_DIR", "/tmp/real-env-data")
    s = Settings()
    assert s.paths.data_dir == Path("/tmp/real-env-data")
