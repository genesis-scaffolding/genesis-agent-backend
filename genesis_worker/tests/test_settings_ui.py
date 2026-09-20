"""Tests for the Settings page render flow (ADR-034).

The Settings page lives at ``genesis_worker/ui/settings.py`` and is run
by Streamlit's app shell which sets ``session_state['worker']`` before
handing control to the page. Tests below mock that fixture.

We use ``streamlit.testing.v1.AppTest`` to render the page; the worker
is wired via a real ``GenesisWorker`` against a tmp state_dir so the
page's calls into the facade hit the actual code paths (file IO,
snapshots, refresh).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from genesis_worker import GenesisWorker
from genesis_worker.settings import PathsSettings, Settings


def _make_worker(tmp_path: Path) -> GenesisWorker:
    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )
    return GenesisWorker(settings=settings)


_SETTINGS_PAGE = Path(__file__).parent.parent / "ui" / "settings.py"


def test_settings_page_renders_without_exception(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    at = AppTest.from_file(str(_SETTINGS_PAGE), default_timeout=30)
    at.session_state["worker"] = worker
    at.run()
    assert not at.exception, f"page crashed: {at.exception}"


def test_settings_page_lists_path_knobs(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    at = AppTest.from_file(str(_SETTINGS_PAGE), default_timeout=30)
    at.session_state["worker"] = worker
    at.run()
    body = "\n".join(str(m.value) for m in at.markdown)
    # Effective settings table renders each knob's env-key form.
    assert "GENESIS_PATHS__VAULT_PATH" in body
    assert "GENESIS_PATHS__DATA_DIR" in body
    assert "GENESIS_SOURCES__HUGGINGFACE__LOCAL_PATH" in body
    # Per-service knobs deliberately absent:
    assert "GENESIS_SERVICES__LLAMA_SWAP__LISTEN_ADDR" not in body


def test_settings_save_calls_refresh_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving the form calls ``worker.refresh_config()``.

    Verifies the page wires to the facade correctly without exercising
    Streamlit's click event (which AppTest handles opaquely). We patch
    the facade methods to record calls.
    """
    worker = _make_worker(tmp_path)
    refresh_calls: list[int] = []
    write_calls: list[dict] = []
    monkeypatch.setattr(
        worker, "write_user_overrides", lambda values: write_calls.append(dict(values))
    )
    monkeypatch.setattr(
        worker,
        "refresh_config",
        lambda: (refresh_calls.append(1), worker._catalog_cache.__setattr__("_v", None))[0],
    )
    monkeypatch.setattr(
        worker,
        "read_user_overrides",
        lambda: {"GENESIS_PATHS__VAULT_PATH": "/srv/vault"},
    )

    at = AppTest.from_file(str(_SETTINGS_PAGE), default_timeout=30)
    at.session_state["worker"] = worker
    at.run()
    assert not at.exception

    # Click each "Override" checkbox to enable editing, then click save.
    for checkbox in at.checkbox:
        checkbox.check()
    at.run()
    button = at.button(key="settings-save-overrides")
    assert button is not None
    button.click()
    at.run()

    assert refresh_calls, "refresh_config was not called after save"
    assert write_calls, "write_user_overrides was not called after save"


@pytest.mark.parametrize(
    "env_key,field_name",
    [
        ("GENESIS_PATHS__VAULT_PATH", "vault_path"),
        ("GENESIS_PATHS__MEDIA_VAULT_PATH", "media_vault_path"),
        ("GENESIS_PATHS__DATA_DIR", "data_dir"),
        ("GENESIS_PATHS__CONFIG_DIR", "config_dir"),
        ("GENESIS_PATHS__CACHE_DIR", "cache_dir"),
        ("GENESIS_PATHS__STATE_DIR", "state_dir"),
        ("GENESIS_PATHS__LOG_DIR", "log_dir"),
    ],
)
def test_each_path_knob_renders_in_effective_table(
    tmp_path: Path, env_key: str, field_name: str
) -> None:
    worker = _make_worker(tmp_path)
    snapshots = worker.snapshot_settings()
    assert any(s.name == f"paths.{field_name}" for s in snapshots), (
        f"snapshot missing path knob {field_name}"
    )
    assert any(s.override_key == env_key for s in snapshots), f"snapshot missing env key {env_key}"
