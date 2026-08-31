"""Seed SillyTavern's ``config.yaml`` so a Docker-published host is reachable.

SillyTavern ships with ``whitelist: [127.0.0.1]`` and
``whitelistDockerHosts: true``. ``whitelistDockerHosts`` resolves
``gateway.docker.internal`` / ``host.docker.internal`` to auto-add the host —
those hostnames only exist on Docker Desktop, so on Docker-CE-on-Linux nothing
is added. A connection published to the container therefore arrives from the
bridge gateway (``172.17.0.1`` by default) and is refused.

This pre-writes ``config.yaml`` (only if absent, so user edits are never
clobbered) that disables the dead auto-host mechanism and whitelists the
detected bridge gateway(s) alongside ``127.0.0.1``. The container entrypoint
copies ``default/config.yaml`` only when the file is missing, and ``npm run
init`` fills missing keys without overwriting existing ones — so our values
survive.

Keeping ``whitelistMode`` on (rather than disabling it) preserves the SSRF
``privateAddressWhitelist``; we only broaden the allowed set to the Docker host.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

_GATEWAY_FALLBACK = "172.17.0.1"


def _bridge_gateways() -> list[str]:
    """Gateway IPs of every Docker bridge network (empty on any failure)."""
    try:
        out = subprocess.run(
            ["docker", "network", "inspect"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    try:
        networks = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(networks, list):
        return []
    gateways: list[str] = []
    for net in networks:
        if net.get("Type") != "bridge":
            continue
        for cfg in (net.get("IPAM") or {}).get("Config") or []:
            gateway = cfg.get("Gateway")
            if isinstance(gateway, str) and gateway:
                gateways.append(gateway)
    return gateways


def seed_config(config_path: Path) -> bool:
    """Write ``config.yaml`` under ``config_path`` if it does not already exist.

    Returns True if a file was written, False if one was already present.
    """
    target = config_path / "config.yaml"
    if target.is_file():
        return False

    gateways = _bridge_gateways() or [_GATEWAY_FALLBACK]
    whitelist = ["127.0.0.1"] + [g for g in gateways if g != "127.0.0.1"]
    payload = {
        "whitelistMode": True,
        "whitelistDockerHosts": False,
        "whitelist": whitelist,
    }

    config_path.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    with tmp.open("w") as f:
        yaml.safe_dump(payload, f, sort_keys=False)
    os.replace(tmp, target)
    return True


__all__ = ["seed_config"]
