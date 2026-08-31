"""Tests for the SillyTavern Docker config-seed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genesis_worker.services.sillytavern.config import seed_config


def _load(config_path: Path) -> dict:
    return yaml.safe_load((config_path / "config.yaml").read_text())


def test_seed_config_writes_when_absent(tmp_path: Path) -> None:
    """First seed writes config.yaml and reports it did."""
    assert seed_config(tmp_path) is True
    assert (tmp_path / "config.yaml").is_file()

    data = _load(tmp_path)
    assert data["whitelistMode"] is True
    assert data["whitelistDockerHosts"] is False
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1"]


def test_seed_config_preserves_other_keys(tmp_path: Path) -> None:
    """A pre-existing config keeps every unrelated key; only whitelist keys are fixed."""
    (tmp_path / "config.yaml").write_text("custom: value\nlisten: false\n")

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["custom"] == "value"
    assert data["listen"] is False
    assert data["whitelistDockerHosts"] is False
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1"]


def test_seed_config_corrects_preexisting_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A config stuck at ST's defaults (whitelistDockerHosts: true) is corrected in place."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.18.0.1"]
    )
    (tmp_path / "config.yaml").write_text(
        "whitelistMode: true\nwhitelist:\n  - 127.0.0.1\nwhitelistDockerHosts: true\n"
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["whitelistDockerHosts"] is False
    assert data["whitelist"] == ["127.0.0.1", "172.18.0.1"]


def test_seed_config_includes_detected_gateways(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Detected bridge gateways are added to the whitelist."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.18.0.1"]
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["whitelist"] == ["127.0.0.1", "172.18.0.1"]


def test_seed_config_falls_back_without_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When no bridges are detected, the common docker0 gateway is used."""
    monkeypatch.setattr("genesis_worker.services.sillytavern.config._bridge_gateways", list)

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1"]


def test_seed_config_is_noop_when_already_correct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A config already carrying the right keys is left byte-for-byte untouched."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.17.0.1"]
    )
    (tmp_path / "config.yaml").write_text(
        "whitelistMode: true\nwhitelistDockerHosts: false\nwhitelist:\n  - 127.0.0.1\n  - 172.17.0.1\n"
    )
    before = (tmp_path / "config.yaml").read_text()

    assert seed_config(tmp_path) is False
    assert (tmp_path / "config.yaml").read_text() == before
