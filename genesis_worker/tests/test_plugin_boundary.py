"""The framework/plugin boundary is a rule, so it gets a test (ADR-009)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PLUGIN_AXES = ("sources", "services")
CONTRACT = "genesis_worker.contracts"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _plugin_modules() -> list[Path]:
    out: list[Path] = []
    for axis in PLUGIN_AXES:
        out.extend(sorted((PACKAGE_ROOT / axis).rglob("*.py")))
    return out


def _package_of(module_path: Path) -> list[str]:
    """Dotted package containing ``module_path`` (its parent dir, __init__ or not)."""
    return ["genesis_worker", *module_path.relative_to(PACKAGE_ROOT).parts[:-1]]


def _imported_modules(tree: ast.AST, module_path: Path) -> list[str]:
    """Every ``genesis_worker.*`` module this file imports, relatives resolved."""
    package = _package_of(module_path)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name.startswith("genesis_worker"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                found.append(".".join([*base, node.module or ""]).rstrip("."))
            elif (node.module or "").startswith("genesis_worker"):
                found.append(node.module or "")
    return found


def _violations(module_path: Path) -> list[str]:
    """Framework modules this plugin file reaches for, beyond the contract."""
    own_plugin = ".".join(_package_of(module_path)[:3])  # genesis_worker.<axis>.<plugin>
    tree = ast.parse(module_path.read_text())
    return [
        name
        for name in _imported_modules(tree, module_path)
        if not name.startswith(CONTRACT) and not name.startswith(own_plugin)
    ]


@pytest.mark.parametrize("module_path", _plugin_modules(), ids=lambda p: p.stem)
def test_plugin_imports_only_the_contract(module_path: Path) -> None:
    """A plugin may import its own package and genesis_worker.contracts. Nothing else."""
    offenders = _violations(module_path)
    assert not offenders, (
        f"{module_path.relative_to(PACKAGE_ROOT)} reaches into the framework: "
        f"{offenders}. Plugins may only import {CONTRACT} (ADR-009)."
    )


def test_the_boundary_check_detects_a_violation(tmp_path: Path) -> None:
    """The walker must actually catch a leak, not silently pass everything."""
    leaky = PACKAGE_ROOT / "services" / "llama_swap" / "_boundary_probe.py"
    leaky.write_text(
        "from ...settings import Settings\n"
        "from ...contracts import Catalog\n"
        "from .options import LlamaSwapOptions\n"
    )
    try:
        assert _violations(leaky) == ["genesis_worker.settings"]
    finally:
        leaky.unlink()


def test_framework_does_not_import_plugin_internals() -> None:
    """The framework talks to plugins through the registries, never their submodules."""
    offenders: list[str] = []
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for name in _imported_modules(tree, path):
            parts = name.split(".")
            # genesis_worker.sources.huggingface.acquire -> reaching inside a plugin
            if len(parts) > 3 and parts[1] in PLUGIN_AXES:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, f"framework reaches into plugin internals: {offenders}"
