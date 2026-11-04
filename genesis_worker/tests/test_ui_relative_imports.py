"""No relative imports in UI modules — Streamlit runs pages as top-level scripts.

Streamlit executes every page script as a module with no parent package,
so a relative import (``from ..utils.installables import ...``) raises
``ImportError: attempted relative import with no known parent package`` at
runtime while the test suite stays green (it imports pages through the
package). UI modules must therefore use absolute ``genesis_worker.*``
imports.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _ui_modules() -> list[Path]:
    modules: list[Path] = []
    for ui_dir in PACKAGE_ROOT.rglob("ui"):
        if not ui_dir.is_dir() or "tests" in ui_dir.relative_to(PACKAGE_ROOT).parts:
            continue
        modules.extend(p for p in sorted(ui_dir.rglob("*.py")) if p.name != "__init__.py")
    return modules


def _relative_imports(module: Path) -> list[str]:
    tree = ast.parse(module.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0:
            name = "." * node.level + (node.module or "")
            found.append(f"{name} (line {node.lineno})")
    return found


def test_ui_modules_use_absolute_imports() -> None:
    violations: list[str] = []
    for module in _ui_modules():
        for hit in _relative_imports(module):
            violations.append(f"{module.relative_to(PACKAGE_ROOT)}: {hit}")
    assert not violations, "UI modules must not use relative imports:\n" + "\n".join(violations)
