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
- ``whitelist`` is guaranteed to contain:
  - ``127.0.0.1`` (loopback)
  - the detected Docker bridge gateway(s) (the health-check source)
  - every IPv4 subnet this host is directly connected to (e.g. the LAN
    subnet ``192.168.8.81/24``) so every peer on the host's physical network
    can reach the container, not just the host itself
  - the Tailscale CGNAT range ``100.64.0.0/10`` so every Tailnet peer --
    phone, other machines on the mesh -- can reach the container without
    per-peer whitelisting
  - the host's own specific interface IPs (kept for log clarity; the CIDRs
    above already include them)
  Any pre-existing user entries are preserved.

``whitelistMode`` is left on (not disabled) so the SSRF
``privateAddressWhitelist`` stays intact -- the set we add is limited to
localhost, the docker bridge gateway, the host's LAN subnet(s), and the
Tailscale overlay, i.e. the host itself plus the devices the user has
explicitly brought into their Tailnet. The container entrypoint copies
``default/config.yaml`` only when the file is missing, and ``npm run init``
fills missing keys without overwriting existing ones, so these in-place
edits survive.

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

# Tailscale assigns every node an address in this CGNAT range; every
# Tailnet peer (phone, another machine on the mesh) is somewhere in here.
# Whitelisting the whole range covers direct WireGuard and DERP-relayed
# paths alike, so the user doesn't have to re-seed every time they add a
# new device to their tailnet.
_TAILSCALE_CGNAT = "100.64.0.0/10"


def _host_connected_subnets() -> list[str]:
    """IPv4 subnets this host is directly connected to (LAN segments).

    Each non-loopback, non-Docker interface contributes its full
    ``addr/prefix_len`` (e.g. ``192.168.8.81/24``), whitelisting every
    peer on the same physical network -- not just the host itself.
    SillyTavern's whitelist supports CIDR notation; if a future version
    drops that, the per-interface IPs still get listed by
    :func:`_host_own_addresses`.
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
        addr_with_prefix = toks[i + 1]  # "192.168.8.81/24"
        if ifname == "lo" or ifname.lower().startswith("docker"):
            continue
        if addr_with_prefix.split("/")[0].startswith("127."):
            continue
        if addr_with_prefix and addr_with_prefix not in found:
            found.append(addr_with_prefix)
    return found


def _host_own_addresses() -> list[str]:
    """IPv4 addresses bound to this host's own interfaces (loopback + docker excluded).

    Kept alongside :func:`_host_connected_subnets` for log clarity -- the
    CIDR form already covers these IPs, but having the bare addresses
    listed makes the whitelist easier to read and falls back gracefully if
    a downstream consumer doesn't honour CIDR matching.
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

    def _accept(allowed: list[str], seen: set[str], value: str) -> bool:
        if value and value not in seen:
            seen.add(value)
            allowed.append(value)
            return True
        return False

    # Build the allowed set: loopback + health-check gateway + host's LAN
    # subnets + host's own addresses + Tailscale CGNAT. Order is cosmetic;
    # membership is what matters.
    allowed: list[str] = []
    seen: set[str] = set()
    _accept(allowed, seen, "127.0.0.1")
    # Fall back to the common docker0 gateway when Docker/network info is
    # unavailable -- the health probe then still lands.
    for gw in _bridge_gateways() or [_GATEWAY_FALLBACK]:
        _accept(allowed, seen, gw)
    # LAN subnets: covers every peer on the host's physical networks, not
    # just the host's own interface IPs.
    for subnet in _host_connected_subnets():
        _accept(allowed, seen, subnet)
    # Host's own interface IPs (for log clarity; the CIDRs above already
    # include them).
    for own in _host_own_addresses():
        _accept(allowed, seen, own)
    # Tailscale CGNAT: covers every Tailnet peer -- phone, other machines
    # on the mesh -- without per-peer whitelisting.
    _accept(allowed, seen, _TAILSCALE_CGNAT)
    # Preserve every entry the user has already put in the file.
    existing = config.get("whitelist")
    for entry in existing if isinstance(existing, list) else []:
        _accept(allowed, seen, str(entry))

    if config.get("whitelist") != allowed:
        config["whitelist"] = allowed
        changed = True

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
