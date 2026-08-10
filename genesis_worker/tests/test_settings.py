"""Tests for genesis_worker.settings."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    s = Settings()
    assert s.paths.resolved_vault_path == s.paths.data_dir / "vault"


def test_settings_resolved_vault_path_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENESIS_PATHS__VAULT_PATH", "/srv/vault")
    s = Settings()
    assert s.paths.resolved_vault_path == Path("/srv/vault")


def test_settings_unknown_env_var_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENESIS_BOGUS", "junk")
    s = Settings()  # must not raise
    assert s is not None


def test_settings_extra_settings_have_defaults() -> None:
    s = Settings()
    assert s.sources.huggingface.default_revision == "main"
    assert s.services.llama_swap.listen_addr == "127.0.0.1:8080"
    assert s.services.llama_swap.session_name == "swap"
    assert s.services.llama_swap.kv_quant_over_bytes == 25_000_000_000


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
