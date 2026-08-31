"""Seed SillyTavern's ``config.yaml`` so a Docker-published host is reachable.

SillyTavern ships with ``whitelist: [127.0.0.1]`` and
``whitelistDockerHosts: true``. ``whitelistDockerHosts`` tries to resolve
``host.docker.internal`` / ``gateway.docker.internal`` to auto-add the host.
Those hostnames only exist on Docker Desktop, so on Docker-CE-on-Linux the
lookups fail with ``ENOTFOUND`` while the real host IP -- the docker bridge
gateway, e.g. ``172.17.0.1`` -- is never whitelisted and every published
connection is refused.

``seed_config`` fixes this in place on every run:

- ``whitelistDockerHosts`` is forced to ``false`` -- kills the doomed hostname
  lookups.
- ``whitelist`` is guaranteed to contain ``127.0.0.1`` plus the detected
  bridge gateway(s); any pre-existing entries are preserved.

``whitelistMode`` is left on (not disabled) so the SSRF
``privateAddressWhitelist`` stays intact -- we only broaden the allowed set to
the Docker host. The container entrypoint copies ``default/config.yaml`` only
when the file is missing, and ``npm run init`` fills missing keys without
overwriting existing ones, so these in-place edits survive.

The fix is deliberately idempotent: a ``config.yaml`` that already exists with
the defaults must still be corrected, otherwise the very default the user is
trying to escape silently defeats the fix. Every other key in the file is
preserved untouched.
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


def _load_config(target: Path) -> dict:
    try:
        data = yaml.safe_load(target.read_text())
    except (yaml.YAMLError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def seed_config(config_path: Path) -> bool:
    """Ensure the whitelist security keys in ``config_path/config.yaml``.

    Returns True if the file was written, False if it was already correct (or
    absent and no write was needed).
    """
    target = config_path / "config.yaml"
    config_path.mkdir(parents=True, exist_ok=True)

    config = _load_config(target)
    changed = False

    if config.get("whitelistDockerHosts") is not False:
        config["whitelistDockerHosts"] = False
        changed = True

    gateways = _bridge_gateways() or [_GATEWAY_FALLBACK]
    whitelist = config.get("whitelist")
    whitelist = whitelist if isinstance(whitelist, list) else []
    for gw in gateways:
        if gw not in whitelist:
            whitelist.append(gw)
            changed = True
    if "127.0.0.1" not in whitelist:
        whitelist = ["127.0.0.1", *whitelist]
        changed = True
    config["whitelist"] = whitelist

    if "whitelistMode" not in config:
        config.setdefault("whitelistMode", True)
        changed = True

    if not changed:
        return False

    tmp = target.with_suffix(f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    with tmp.open("w") as f:
        yaml.safe_dump(config, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, target)
    return True


__all__ = ["seed_config"]
