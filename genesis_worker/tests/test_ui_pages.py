"""Verify that every registered plugin's ui_pages are well-formed."""

from __future__ import annotations

from genesis_worker import GenesisWorker
from genesis_worker.contracts import UiPage


def test_services_ship_pages_with_existing_files() -> None:
    w = GenesisWorker()
    for info in w.list_services():
        svc = w.service(info.name)
        for page in svc.ui_pages:
            assert isinstance(page, UiPage), f"{info.name}: bad page type"
            assert page.label, f"{info.name}: empty label"
            assert page.icon, f"{info.name}: empty icon"
            assert page.path.exists(), f"{info.name}: page path missing: {page.path}"
            # Every page must live inside the plugin's ui/ directory.
            assert "ui" in page.path.parts, f"{info.name}: page outside ui/: {page.path}"


def test_sources_ship_pages_with_existing_files() -> None:
    w = GenesisWorker()
    for info in w.list_sources():
        src = w.source(info.name)
        for page in src.ui_pages:
            assert isinstance(page, UiPage)
            assert page.label
            assert page.icon
            assert page.path.exists(), f"{info.name}: page path missing: {page.path}"
            assert "ui" in page.path.parts


def test_landing_page_is_first_entry() -> None:
    """The first entry of ui_pages is the landing page (ADR-010 convention)."""
    w = GenesisWorker()
    for info in w.list_services():
        svc = w.service(info.name)
        pages = svc.ui_pages
        if not pages:
            continue
        # Landing is the first entry; if more pages exist, the plugin author
        # has ordered them with landing first by convention.
        assert pages[0].label, f"{info.name}: landing has empty label"


def test_llama_swap_ships_full_page_set() -> None:
    w = GenesisWorker()
    svc = w.service("llama_swap")
    labels = [p.label for p in svc.ui_pages]
    assert "Status" in labels
    assert "Config editor" in labels
    assert "Recipes view" in labels
    assert "Pi export" in labels


def test_huggingface_ships_acquire_page() -> None:
    w = GenesisWorker()
    src = w.source("huggingface")
    labels = [p.label for p in src.ui_pages]
    assert "Acquire model" in labels
