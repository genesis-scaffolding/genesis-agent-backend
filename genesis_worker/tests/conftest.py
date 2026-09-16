"""Session-level isolation from the host environment.

The user-overrides file at ``<config_dir>/user-overrides.env``
(ADR-034) and the XDG-derived directories on the host leak into
``Settings()`` construction and pollute tests. This conftest
redirects XDG paths to session-scoped tmpdirs so every test
constructs ``Settings`` against a known-empty base.

Tests that need a real ``config_dir`` should pass
``paths=PathsSettings(config_dir=<tmpdir>)`` explicitly; that
short-circuits the XDG lookup.
"""

from __future__ import annotations

import os

import pytest

_OVERRIDE_KEYS = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
)


@pytest.fixture(autouse=True, scope="session")
def _isolate_xdg_paths(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Point XDG base dirs at session-scoped tmpdirs for the whole test run."""
    for key in _OVERRIDE_KEYS:
        os.environ[key] = str(tmp_path_factory.mktemp(f"xdg-{key.lower()}"))
