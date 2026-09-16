"""Seed the ``whitelist`` key of a YAML config so a Docker-published host is reachable.

Lifted from ``services/sillytavern/config.py`` (ADR-035 phase 2) so any
docker service whose container respects an IP whitelist can reuse the
seeding logic. The original module is going away in phase 3 — sillytavern
moves to YAML and the whitelist seeding becomes a generic pre-start hook
in ``utils/services/hooks.py``.

The fix is deliberately idempotent: a ``config.yaml`` that already exists
with the defaults must still be corrected, otherwise the very default the
user is trying to escape silently defeats the fix. Every other key in the
file is preserved untouched.

Tailscale CGNAT (``100.64.0.0/10``) lives in :mod:`genesis_worker.utils.net.constants`
so other code can use it without re-declaring the magic string.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

from .net.constants import TAILSCALE_CGNAT

_GATEWAY_FALLBACK = "172.17.0.1"


def _host_connected_subnets() -> list[str]:
    """IPv4 subnets this host is directly connected to (LAN segments).

    Each non-loopback, non-Docker interface contributes its full
    ``addr/prefix_len`` (e.g. ``192.168.8.81/24``), whitelisting every
    peer on the same physical network -- not just the host itself.
    """
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    found: list[str] = []
    for line in out.splitlines():
        toks = line.split()
        if "inet" not in toks:
            continue
        i = toks.index("inet")
        ifname = toks[i - 1].rstrip(":")
        addr_with_prefix = toks[i + 1]
        if ifname == "lo" or ifname.lower().startswith("docker"):
            continue
        if addr_with_prefix.split("/")[0].startswith("127."):
            continue
        if addr_with_prefix and addr_with_prefix not in found:
            found.append(addr_with_prefix)
    return found


def _host_own_addresses() -> list[str]:
    """IPv4 addresses bound to this host's own interfaces (loopback + docker excluded)."""
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    found: list[str] = []
    for line in out.splitlines():
        toks = line.split()
        if "inet" not in toks:
            continue
        i = toks.index("inet")
        ifname = toks[i - 1].rstrip(":")
        addr = toks[i + 1].split("/")[0]
        if ifname == "lo" or ifname.lower().startswith("docker"):
            continue
        if addr and addr != "127.0.0.1" and addr not in found:
            found.append(addr)
    return found


def _bridge_gateways() -> list[str]:
    """Gateway IPs of every Docker bridge network (empty on any failure).

    Inspects every network id rather than calling ``inspect`` with no arg
    (which errors), and matches the ``bridge`` network by its ``Driver``
    field -- some Docker daemons omit the ``Type`` key entirely.
    """
    ids_out = subprocess.run(
        ["docker", "network", "ls", "-q"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout
    ids = [line.strip() for line in ids_out.splitlines() if line.strip()]
    if not ids:
        return []
    try:
        out = subprocess.run(
            ["docker", "network", "inspect", *ids],
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
        if net.get("Driver") != "bridge":
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


def _accept(allowed: list[str], seen: set[str], value: str) -> bool:
    if value and value not in seen:
        seen.add(value)
        allowed.append(value)
        return True
    return False


def seed_yaml_whitelist(
    target: Path,
    *,
    key: str,
    extras: list[str] | None = None,
    disable_docker_hosts: bool = True,
) -> bool:
    """Seed ``target``'s whitelist-style ``key`` so the host can reach the container.

    Writes ``target/config.yaml`` (creating it when absent) with:

    - ``<key>DockerHosts`` set to ``False`` (when ``disable_docker_hosts``)
      -- kills the doomed ``host.docker.internal`` / ``gateway.docker.internal``
      lookups on Docker-CE-on-Linux.
    - ``<key>`` containing loopback + docker bridge gateway(s) + host LAN
      subnets + host own addresses + the Tailscale CGNAT range +
      pre-existing user entries.

    Returns True if the file was written, False if it was already correct
    (or absent and no write was needed).
    """
    target.mkdir(parents=True, exist_ok=True)
    config_file = target / "config.yaml"

    config = _load_config(config_file)
    changed = False

    if disable_docker_hosts and config.get(f"{key}DockerHosts") is not False:
        config[f"{key}DockerHosts"] = False
        changed = True

    allowed: list[str] = []
    seen: set[str] = set()
    _accept(allowed, seen, "127.0.0.1")
    for gw in _bridge_gateways() or [_GATEWAY_FALLBACK]:
        _accept(allowed, seen, gw)
    for subnet in _host_connected_subnets():
        _accept(allowed, seen, subnet)
    for own in _host_own_addresses():
        _accept(allowed, seen, own)
    _accept(allowed, seen, TAILSCALE_CGNAT)
    for entry in extras or []:
        _accept(allowed, seen, str(entry))
    existing = config.get(key)
    for entry in existing if isinstance(existing, list) else []:
        _accept(allowed, seen, str(entry))

    if config.get(key) != allowed:
        config[key] = allowed
        changed = True

    if not changed:
        return False

    tmp = config_file.with_suffix(f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    with tmp.open("w") as f:
        yaml.safe_dump(config, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, config_file)
    return True


__all__ = ["seed_yaml_whitelist"]
