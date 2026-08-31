"""Tests for the SillyTavern Docker config-seed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genesis_worker.services.sillytavern.config import seed_config


def test_seed_config_writes_when_absent(tmp_path: Path) -> None:
    """First seed writes config.yaml and reports it did."""
    written = seed_config(tmp_path)
    config_file = tmp_path / "config.yaml"
    assert written is True
    assert config_file.is_file()

    data = yaml.safe_load(config_file.read_text())
    assert data["whitelistMode"] is True
    assert data["whitelistDockerHosts"] is False
    assert "127.0.0.1" in data["whitelist"]


def test_seed_config_skips_existing(tmp_path: Path) -> None:
    """A pre-existing config.yaml is never clobbered."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("custom: value\n")

    written = seed_config(tmp_path)
    assert written is False
    assert yaml.safe_load(config_file.read_text()) == {"custom": "value"}


def test_seed_config_includes_detected_gateways(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Detected bridge gateways are added to the whitelist."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.18.0.1"]
    )

    seed_config(tmp_path)
    data = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert data["whitelist"] == ["127.0.0.1", "172.18.0.1"]


def test_seed_config_falls_back_without_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When no bridges are detected, the common docker0 gateway is used."""
    monkeypatch.setattr("genesis_worker.services.sillytavern.config._bridge_gateways", list)

    seed_config(tmp_path)
    data = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1"]
