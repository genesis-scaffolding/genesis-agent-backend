"""Parallel to ``test_plugin_boundary.py`` for YAML-declared services.

Phase 2 ships an empty ``services/_declarative/`` (the marker package
gets the empty ``__init__.py`` so ``importlib.resources`` can resolve
it). The walker must see an empty dir and pass; phase 3 adds the real
YAMLs and this test asserts they construct.
"""

from __future__ import annotations

from pathlib import Path

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


def test_declarative_dir_exists() -> None:
    """Phase 2 puts an ``__init__.py`` marker here so ``importlib.resources`` resolves it."""
    assert _DECLARATIVE_DIR.is_dir()
    assert (_DECLARATIVE_DIR / "__init__.py").is_file()


def test_no_yaml_specs_yet() -> None:
    """Phase 2: declarative dir has the marker only. Phase 3 adds the YAMLs."""
    yamls = sorted(_DECLARATIVE_DIR.glob("*.yaml"))
    assert yamls == [], (
        f"phase 3 will introduce YAMLs here; phase 2 should ship empty. found: {yamls}"
    )


def test_declarative_marker_init_is_empty() -> None:
    """The marker must not import anything -- it's a Python module, not a plugin.

    Mirrors the contract ``test_plugin_boundary.py`` enforces for every
    ``__init__.py`` under ``services/``.
    """
    import ast

    init_text = (_DECLARATIVE_DIR / "__init__.py").read_text()
    tree = ast.parse(init_text)
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert imports == [], (
        f"services/_declarative/__init__.py must have no imports; found: {ast.dump(tree)}"
    )


def test_declarative_dir_has_no_python_modules_other_than_init() -> None:
    """Anything else under ``services/_declarative/`` would be a module the plugin
    boundary test walks. We want zero non-init modules so the boundary test passes
    without surprises; YAMLs are invisible to it.
    """
    found = [p for p in _DECLARATIVE_DIR.glob("*.py") if p.name != "__init__.py"]
    assert found == [], f"unexpected python modules: {found}"
