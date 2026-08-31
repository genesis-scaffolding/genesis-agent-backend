"""Smoke tests for the Service Catalog UI page (ADR-029)."""

from __future__ import annotations

import ast
from pathlib import Path


_PAGE = Path(__file__).resolve().parents[1] / "ui" / "services_catalog.py"


def test_services_catalog_page_parses() -> None:
    """The page must be valid Python — Streamlit executes it top-level."""
    ast.parse(_PAGE.read_text())


def test_services_catalog_page_does_not_import_plugins() -> None:
    """Boundary: the framework catalog page must not reach into plugin internals.

    It can import from ``genesis_worker.contracts`` and from
    ``genesis_worker`` (the facade via ``st.session_state['worker']``),
    but never ``genesis_worker.services.*`` — that would be the boundary
    violation ADR-009 prohibits.
    """
    tree = ast.parse(_PAGE.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        target = node
        if isinstance(node, ast.ImportFrom):
            target = node.module or ""
        elif isinstance(node, ast.Import):
            target = " ".join(alias.name for alias in node.names)
        else:
            continue
        if target.startswith("genesis_worker.services"):
            offenders.append(target)
    assert not offenders, f"page imports plugin internals: {offenders}"
