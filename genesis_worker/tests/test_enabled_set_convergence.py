"""Enabled-set convergence across the worker's two processes (ADR-054).

The worker runs as two processes that share the state file and no
memory: the Streamlit UI flips the set, the FastAPI API reads it. These
tests model that with two ``GenesisWorker`` instances over one
``state_dir`` — one file, two private copies of the state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genesis_worker import GenesisWorker
from genesis_worker.api import deps
from genesis_worker.api.app import create_app
from genesis_worker.registries import ServiceRegistry
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.utils.state.enabled_services import load_enabled_set, save_enabled_set


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )


def _seed_enabled_set(tmp_path: Path, names: set[str]) -> Path:
    """Write a known enabled set so the ADR-029 bootstrap never runs.

    The bootstrap probes ``is_available()``, which depends on what is
    installed on the host — a seeded file keeps these tests hermetic.
    """
    state_dir = tmp_path / "state"
    save_enabled_set(state_dir, names)
    return state_dir / "enabled_services.yaml"


# --- the proof test ---------------------------------------------------------


def test_api_services_reflects_ui_enable_without_restart(tmp_path: Path) -> None:
    """A flip in the UI process is visible to the API process without a restart."""
    settings = _settings(tmp_path)
    _seed_enabled_set(tmp_path, set())

    api_worker = GenesisWorker(settings=settings)  # the API process, started earlier
    ui_worker = GenesisWorker(settings=settings)  # the UI process

    deps._worker = api_worker
    try:
        with TestClient(create_app()) as client:
            before = {s["name"]: s["is_enabled"] for s in client.get("/v1/services").json()}
            assert before["llama_swap"] is False

            ui_worker.services.enable("llama_swap")  # the UI flip

            after = {s["name"]: s["is_enabled"] for s in client.get("/v1/services").json()}
            assert after["llama_swap"] is True
    finally:
        deps._worker = None


def test_api_services_reflects_ui_disable_without_restart(tmp_path: Path) -> None:
    """The disable direction converges too."""
    settings = _settings(tmp_path)
    _seed_enabled_set(tmp_path, {"llama_swap"})

    api_worker = GenesisWorker(settings=settings)
    ui_worker = GenesisWorker(settings=settings)

    deps._worker = api_worker
    try:
        with TestClient(create_app()) as client:
            before = {s["name"]: s["is_enabled"] for s in client.get("/v1/services").json()}
            assert before["llama_swap"] is True

            ui_worker.services.disable("llama_swap")

            after = {s["name"]: s["is_enabled"] for s in client.get("/v1/services").json()}
            assert after["llama_swap"] is False
    finally:
        deps._worker = None


# --- registry level ----------------------------------------------------------


def test_registry_adopts_enabled_set_written_by_another_process(tmp_path: Path) -> None:
    """Two registries over one state dir see each other's flips."""
    settings = _settings(tmp_path)
    _seed_enabled_set(tmp_path, set())

    api_reg = ServiceRegistry(settings)
    ui_reg = ServiceRegistry(settings)

    assert not api_reg.is_enabled("llama_swap")
    ui_reg.enable("llama_swap")
    assert api_reg.is_enabled("llama_swap")
    assert "llama_swap" in api_reg.enabled_names()
    assert [svc.name for svc in api_reg.enabled()] == ["llama_swap"]


def test_enable_applies_to_the_freshest_file_content(tmp_path: Path) -> None:
    """A stale writer does not clobber another process's flip (read-modify-write)."""
    settings = _settings(tmp_path)
    _seed_enabled_set(tmp_path, set())

    reg_a = ServiceRegistry(settings)
    reg_b = ServiceRegistry(settings)

    reg_a.enable("llama_swap")
    reg_b.enable("sillytavern")

    assert load_enabled_set(tmp_path / "state") == {"llama_swap", "sillytavern"}
    assert reg_a.enabled_names() == {"llama_swap", "sillytavern"}
    assert reg_b.enabled_names() == {"llama_swap", "sillytavern"}


def test_disable_running_guard_refuses_and_leaves_the_set_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The running-guard still fires first, and the file is not rewritten."""
    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "is_running", lambda self: True)

    settings = _settings(tmp_path)
    state_file = _seed_enabled_set(tmp_path, {"llama_swap"})
    reg = ServiceRegistry(settings)
    before = state_file.read_text()

    with pytest.raises(RuntimeError, match="running"):
        reg.disable("llama_swap")

    assert reg.is_enabled("llama_swap")
    assert state_file.read_text() == before


def test_refresh_does_not_rebootstrap_when_the_state_file_disappears(tmp_path: Path) -> None:
    """A deleted file leaves the last known set in place — no silent re-enable."""
    settings = _settings(tmp_path)
    state_file = _seed_enabled_set(tmp_path, {"llama_swap"})
    reg = ServiceRegistry(settings)

    state_file.unlink()

    assert reg.is_enabled("llama_swap")
    assert not state_file.exists()


def test_refresh_keeps_the_known_set_when_the_state_file_is_corrupt(tmp_path: Path) -> None:
    """A corrupt file leaves the last known set in place — no wipe."""
    settings = _settings(tmp_path)
    state_file = _seed_enabled_set(tmp_path, {"llama_swap"})
    reg = ServiceRegistry(settings)

    state_file.write_text("enabled: [unclosed\n")

    assert reg.is_enabled("llama_swap")
    assert state_file.read_text() == "enabled: [unclosed\n"


def test_bootstrap_adopts_a_publish_landing_after_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A publish landing between the bootstrap write and the first read wins."""
    from genesis_worker.registries import save_enabled_set as registry_save

    def save_then_compete(state_dir: Path, names: set[str]) -> None:
        registry_save(state_dir, names)
        # Another process publishes right after our write.
        registry_save(state_dir, {"sillytavern"})

    monkeypatch.setattr("genesis_worker.registries.save_enabled_set", save_then_compete)

    settings = _settings(tmp_path)
    reg = ServiceRegistry(settings)

    assert reg.is_enabled("sillytavern") is True
    assert reg.is_enabled("llama_swap") is False


def test_enable_reconciles_the_cache_when_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed write must not leave the cache claiming a state the file lacks."""
    settings = _settings(tmp_path)
    _seed_enabled_set(tmp_path, set())
    reg = ServiceRegistry(settings)

    def failing_save(state_dir: Path, names: set[str]) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr("genesis_worker.registries.save_enabled_set", failing_save)

    with pytest.raises(OSError):
        reg.enable("llama_swap")

    # The next read reconciles the cache against what is on disk.
    assert reg.is_enabled("llama_swap") is False


# --- the API response --------------------------------------------------------


def test_services_response_uses_one_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One snapshot per response: a flip mid-request can't split the list."""
    counts = {"enabled_names": 0, "is_enabled": 0}
    original_names = ServiceRegistry.enabled_names
    original_is_enabled = ServiceRegistry.is_enabled

    def counting_enabled_names(self: ServiceRegistry) -> set[str]:
        counts["enabled_names"] += 1
        return original_names(self)

    def counting_is_enabled(self: ServiceRegistry, name: str) -> bool:
        counts["is_enabled"] += 1
        return original_is_enabled(self, name)

    monkeypatch.setattr(ServiceRegistry, "enabled_names", counting_enabled_names)
    monkeypatch.setattr(ServiceRegistry, "is_enabled", counting_is_enabled)

    settings = _settings(tmp_path)
    _seed_enabled_set(tmp_path, {"llama_swap"})
    worker = GenesisWorker(settings=settings)
    deps._worker = worker
    try:
        with TestClient(create_app()) as client:
            response = client.get("/v1/services")
    finally:
        deps._worker = None

    assert response.status_code == 200
    assert counts["enabled_names"] == 1
    assert counts["is_enabled"] == 0
