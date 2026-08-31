"""Tests for the SillyTavern Docker config-seed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from genesis_worker.services.sillytavern.config import (
    _host_connected_subnets,
    _host_own_addresses,
    seed_config,
)


def _load(config_path: Path) -> dict:
    return yaml.safe_load((config_path / "config.yaml").read_text())


def test_seed_config_writes_when_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """First seed writes config.yaml and reports it did."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )
    assert seed_config(tmp_path) is True
    assert (tmp_path / "config.yaml").is_file()

    data = _load(tmp_path)
    assert data["whitelistMode"] is True
    assert data["whitelistDockerHosts"] is False
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1", "100.64.0.0/10"]


def test_seed_config_preserves_other_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A pre-existing config keeps every unrelated key; only whitelist keys are fixed."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )
    (tmp_path / "config.yaml").write_text("custom: value\nlisten: false\n")

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["custom"] == "value"
    assert data["listen"] is False
    assert data["whitelistDockerHosts"] is False
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1", "100.64.0.0/10"]


def test_seed_config_corrects_preexisting_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A config stuck at ST's defaults (whitelistDockerHosts: true) is corrected in place."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.18.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )
    (tmp_path / "config.yaml").write_text(
        "whitelistMode: true\nwhitelist:\n  - 127.0.0.1\nwhitelistDockerHosts: true\n"
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["whitelistDockerHosts"] is False
    assert data["whitelist"] == ["127.0.0.1", "172.18.0.1", "100.64.0.0/10"]


def test_seed_config_includes_detected_gateways(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Detected bridge gateways are added to the whitelist."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.18.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["whitelist"] == ["127.0.0.1", "172.18.0.1", "100.64.0.0/10"]


def test_seed_config_falls_back_without_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When no bridges are detected, the common docker0 gateway is used."""
    monkeypatch.setattr("genesis_worker.services.sillytavern.config._bridge_gateways", list)
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1", "100.64.0.0/10"]


def test_seed_config_includes_host_own_addresses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The host's own interface addresses are added so host-local (LAN IP) access works."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.17.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses", lambda: ["192.168.8.81"]
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert data["whitelist"] == ["127.0.0.1", "172.17.0.1", "192.168.8.81", "100.64.0.0/10"]


def test_seed_config_includes_lan_subnets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Host LAN subnets are added so peers on the host's physical network can connect."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.17.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        lambda: ["192.168.8.81/24"],
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        lambda: ["192.168.8.81"],
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    # Both the CIDR subnet and the host's bare IP end up in the whitelist.
    assert "192.168.8.81/24" in data["whitelist"]
    assert "192.168.8.81" in data["whitelist"]


def test_seed_config_includes_tailscale_cgnat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Tailscale CGNAT range is added so any Tailnet peer can connect."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.17.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    assert "100.64.0.0/10" in data["whitelist"]


def test_seed_config_preserves_user_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User-added whitelist entries not produced by the seeders are preserved."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.17.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )
    (tmp_path / "config.yaml").write_text(
        "whitelist:\n  - 127.0.0.1\n  - 172.17.0.1\n  - 192.0.2.42\n"
        "whitelistDockerHosts: false\nwhitelistMode: true\n"
    )

    assert seed_config(tmp_path) is True
    data = _load(tmp_path)
    # 192.0.2.42 is a user entry; it must survive the rewrite.
    assert "192.0.2.42" in data["whitelist"]
    assert "100.64.0.0/10" in data["whitelist"]


def test_seed_config_is_noop_when_already_correct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A config already carrying the right keys is left byte-for-byte untouched."""
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._bridge_gateways", lambda: ["172.17.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_connected_subnets",
        list,
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config._host_own_addresses",
        list,
    )
    (tmp_path / "config.yaml").write_text(
        "whitelistMode: true\nwhitelistDockerHosts: false\n"
        "whitelist:\n  - 127.0.0.1\n  - 172.17.0.1\n  - 100.64.0.0/10\n"
    )
    before = (tmp_path / "config.yaml").read_text()

    assert seed_config(tmp_path) is False
    assert (tmp_path / "config.yaml").read_text() == before


def test_host_own_addresses_parses_interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Picks the host's own routable IPv4 addresses and skips lo + docker bridges."""
    sample = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: wlan0    inet 192.168.8.81/24 brd 192.168.8.255 scope global wlan0\n"
        "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config.subprocess.run",
        lambda *a, **k: MagicMock(stdout=sample),
    )
    assert _host_own_addresses() == ["192.168.8.81"]


def test_host_own_addresses_returns_empty_when_ip_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a, **_k) -> str:
        raise FileNotFoundError("no ip binary")

    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config.subprocess.run",
        boom,
    )
    assert _host_own_addresses() == []


def test_host_connected_subnets_parses_interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Picks LAN subnets in CIDR form; skips lo + docker + loopback ranges."""
    sample = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: wlan0    inet 192.168.8.81/24 brd 192.168.8.255 scope global wlan0\n"
        "3: eth0    inet 10.42.0.5/16 brd 10.42.255.255 scope global eth0\n"
        "4: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
    )
    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config.subprocess.run",
        lambda *a, **k: MagicMock(stdout=sample),
    )
    assert _host_connected_subnets() == ["192.168.8.81/24", "10.42.0.5/16"]


def test_host_connected_subnets_returns_empty_when_ip_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a, **_k) -> str:
        raise FileNotFoundError("no ip binary")

    monkeypatch.setattr(
        "genesis_worker.services.sillytavern.config.subprocess.run",
        boom,
    )
    assert _host_connected_subnets() == []
