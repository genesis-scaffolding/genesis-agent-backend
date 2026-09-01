"""Tests for ``ServiceRegistry`` enable/disable + bootstrap (ADR-029).

Constructs a real ``ServiceRegistry`` against an isolated ``state_dir`` so
tests don't pollute ``~/.local/state/genesis-worker/enabled_services.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.registries import ServiceRegistry
from genesis_worker.settings import PathsSettings, Settings


def _settings(tmp_path: Path) -> Settings:
    """Build Settings that point state_dir at ``tmp_path`` (test-isolated)."""
    return Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )


# --- bootstrap ---------------------------------------------------------------


def test_bootstrap_auto_enables_installed_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First run: every service reporting ``is_available() == True`` is enabled."""
    from genesis_worker.services.llama_swap import LlamaSwapService
    from genesis_worker.services.sillytavern import SillyTavernService

    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: True)
    monkeypatch.setattr(SillyTavernService, "is_available", lambda self: False)

    reg = ServiceRegistry(_settings(tmp_path))

    assert reg.is_enabled("llama_swap")
    assert not reg.is_enabled("sillytavern")


def test_bootstrap_persists_initial_state(tmp_path: Path) -> None:
    """After bootstrap, the state file exists with the resolved set."""
    ServiceRegistry(_settings(tmp_path))
    state_file = tmp_path / "state" / "enabled_services.yaml"
    assert state_file.is_file()


def test_bootstrap_skipped_when_state_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing state file is the source of truth — install state is not re-probed."""
    from genesis_worker.services.llama_swap import LlamaSwapService

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "enabled_services.yaml").write_text("enabled:\n  - llama_swap\n")

    # Even if every other service is now "installed", the persisted set is honored.
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: True)

    reg = ServiceRegistry(_settings(tmp_path))
    assert reg.is_enabled("llama_swap")


# --- enable / disable --------------------------------------------------------


def test_enable_is_idempotent_and_persists(tmp_path: Path) -> None:
    reg = ServiceRegistry(_settings(tmp_path))
    reg.enable("llama_swap")
    reg.enable("llama_swap")  # second call should not error
    assert reg.is_enabled("llama_swap")

    # Persistence: a fresh registry reads the same set.
    reg2 = ServiceRegistry(_settings(tmp_path))
    assert reg2.is_enabled("llama_swap")


def test_enable_unknown_service_raises_keyerror(tmp_path: Path) -> None:
    reg = ServiceRegistry(_settings(tmp_path))
    with pytest.raises(KeyError):
        reg.enable("does_not_exist")


def test_disable_marks_disabled_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "is_running", lambda self: False)

    reg = ServiceRegistry(_settings(tmp_path))
    reg.enable("llama_swap")
    reg.disable("llama_swap")
    assert not reg.is_enabled("llama_swap")

    reg2 = ServiceRegistry(_settings(tmp_path))
    assert not reg2.is_enabled("llama_swap")


def test_disable_raises_when_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The framework-level running guard mirrors the UI's disabled toggle."""
    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "is_running", lambda self: True)

    reg = ServiceRegistry(_settings(tmp_path))
    reg.enable("llama_swap")
    with pytest.raises(RuntimeError, match="running"):
        reg.disable("llama_swap")
    # And the state didn't change.
    assert reg.is_enabled("llama_swap")


def test_disable_unknown_service_raises_keyerror(tmp_path: Path) -> None:
    reg = ServiceRegistry(_settings(tmp_path))
    with pytest.raises(KeyError):
        reg.disable("does_not_exist")


def test_disable_when_already_disabled_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "is_running", lambda self: False)

    reg = ServiceRegistry(_settings(tmp_path))
    reg.disable("llama_swap")  # never enabled — must not raise
    assert not reg.is_enabled("llama_swap")


# --- enabled() / disabled() / enabled_names() -------------------------------


def test_enabled_and_disabled_partition_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enabled() and disabled() partition every discovered service."""
    from genesis_worker.services.llama_swap import LlamaSwapService
    from genesis_worker.services.sillytavern import SillyTavernService

    # Force the bootstrap to a known state: nothing installed → nothing
    # auto-enabled. We then explicitly enable one and disable it.
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    monkeypatch.setattr(SillyTavernService, "is_available", lambda self: False)
    monkeypatch.setattr(LlamaSwapService, "is_running", lambda self: False)
    monkeypatch.setattr(SillyTavernService, "is_running", lambda self: False)

    reg = ServiceRegistry(_settings(tmp_path))
    reg.enable("llama_swap")
    reg.disable("llama_swap")  # round trip via disable

    enabled_names = {s.name for s in reg.enabled()}
    disabled_names = {s.name for s in reg.disabled()}
    all_names = {s.name for s in reg.all()}
    assert enabled_names.isdisjoint(disabled_names)
    assert enabled_names | disabled_names == all_names


def test_enabled_names_returns_a_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The returned set must not let callers mutate the registry's state."""
    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: True)

    reg = ServiceRegistry(_settings(tmp_path))
    snapshot = reg.enabled_names()
    snapshot.discard("llama_swap")
    # Registry is unaffected.
    assert reg.is_enabled("llama_swap")
