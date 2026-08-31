"""Smoke test for the Streamlit app shell's page-discovery logic."""

from __future__ import annotations

from pathlib import Path

from genesis_worker import GenesisWorker
from genesis_worker.ui.app import _FRAMEWORK_UI


def test_framework_ui_dir_exists() -> None:
    assert _FRAMEWORK_UI.is_dir()
    assert (_FRAMEWORK_UI / "dashboard.py").exists()
    assert (_FRAMEWORK_UI / "catalog.py").exists()
    assert (_FRAMEWORK_UI / "services_catalog.py").exists()
    assert (_FRAMEWORK_UI / "app.py").exists()


def test_dashboard_references_services_catalog() -> None:
    """The dashboard's 'Manage services' button must point at services_catalog.py.

    Guards against future renames of the catalog page breaking the in-page
    navigation silently (the sidebar would still link, but the dashboard
    button would 404).
    """
    source = (_FRAMEWORK_UI / "dashboard.py").read_text()
    assert "services_catalog.py" in source
    assert "switch_page" in source


def test_page_discovery_resolves_all_paths() -> None:
    """Walk every registered plugin and confirm the page paths exist."""
    w = GenesisWorker()
    framework_pages = [
        _FRAMEWORK_UI / "dashboard.py",
        _FRAMEWORK_UI / "catalog.py",
        _FRAMEWORK_UI / "services_catalog.py",
    ]
    plugin_paths: list[Path] = []
    for info in w.list_services():
        for p in w.service(info.name).ui_pages:
            plugin_paths.append(p.path)
    for info in w.list_sources():
        for p in w.source(info.name).ui_pages:
            plugin_paths.append(p.path)

    all_paths = framework_pages + plugin_paths
    assert all_paths, "no pages discovered"
    for path in all_paths:
        assert path.exists(), f"page path missing: {path}"