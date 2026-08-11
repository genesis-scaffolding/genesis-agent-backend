"""Smoke tests: every CLI module loads and --help exits 0."""

from __future__ import annotations

import subprocess
import sys

import pytest

CLI_MODULES = [
    "genesis_worker.cli.up",
    "genesis_worker.cli.catalog",
    "genesis_worker.cli.config",
    "genesis_worker.cli.pi_models",
]


@pytest.mark.parametrize("module", CLI_MODULES)
def test_cli_help_exits_zero(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, f"{module} --help failed: {result.stderr}"


def test_cli_ui_help_exits_zero() -> None:
    """The UI CLI has no --help (it shells out to streamlit); but importing it must work."""
    import genesis_worker.cli.ui  # noqa: F401