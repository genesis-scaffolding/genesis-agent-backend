"""Smoke checks for the Crawl4AI service's UI pages.

Streamlit pages execute top-level code on import, which makes a normal
``import`` test unreliable. We use :func:`ast.parse` to catch syntax
errors and bad top-level constructs without running the code. The
companion :func:`test_page_discovery_resolves_all_paths` in
``test_app_shell.py`` covers the "page file exists" side.
"""

from __future__ import annotations

import ast
from pathlib import Path

PAGES = [
    Path(__file__).resolve().parents[1] / "services" / "crawl4ai" / "ui" / "status.py",
    Path(__file__).resolve().parents[1] / "services" / "crawl4ai" / "ui" / "image.py",
]


def _parse(page: Path) -> ast.Module:
    return ast.parse(page.read_text())


def _has_service_name(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SERVICE_NAME" for t in node.targets)
        for node in tree.body
    )


def test_status_page_parses() -> None:
    page = PAGES[0]
    assert _has_service_name(_parse(page)), f"{page.name} must define SERVICE_NAME"


def test_image_page_parses() -> None:
    page = PAGES[1]
    assert _has_service_name(_parse(page)), f"{page.name} must define SERVICE_NAME"
