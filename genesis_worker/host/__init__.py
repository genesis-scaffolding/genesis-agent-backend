"""Static-ish host information for the dashboard.

Distinct from ``metrics``: host info changes only on system events
(boot, network reconnect), while metrics are continuously varying.
"""

from __future__ import annotations

import platform
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class HostInfo:
    hostname: str
    os: str            # e.g., "Linux 6.5.0-arch1-1"
    arch: str          # e.g., "x86_64"
    python: str        # e.g., "3.11.7"
    uptime_s: int | None
    public_ip: str | None
    tailscale_ip: str | None


def collect_host_info() -> HostInfo:
    """Read host info. Each IO-dependent field is None on failure."""
    hostname = socket.gethostname()
    os_str = f"{platform.system()} {platform.release()}"
    arch = platform.machine()
    python = platform.python_version()

    uptime_s: int | None = None
    try:
        import psutil

        uptime_s = int(time.time() - psutil.boot_time())
    except Exception:  # noqa: BLE001, S110 — psutil is best-effort
        pass

    # api.ipify.org: simple text response, no auth, well-known service.
    public_ip: str | None = None
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=2) as resp:
            public_ip = resp.read().decode().strip() or None
    except Exception:  # noqa: BLE001, S110 — no network / DNS / 5xx; field is optional
        pass

    tailscale_ip: str | None = None
    if shutil.which("tailscale"):
        try:
            out = subprocess.run(
                ["tailscale", "ip", "-1"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if out.returncode == 0:
                tailscale_ip = out.stdout.strip() or None
        except Exception:  # noqa: BLE001, S110 — tailscale may be installed but not running
            pass

    return HostInfo(
        hostname=hostname,
        os=os_str,
        arch=arch,
        python=python,
        uptime_s=uptime_s,
        public_ip=public_ip,
        tailscale_ip=tailscale_ip,
    )


__all__ = ["HostInfo", "collect_host_info"]
