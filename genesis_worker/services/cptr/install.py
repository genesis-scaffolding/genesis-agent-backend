"""Installable for cptr — driven by ``uv tool install`` rather than a tarball download.

uv is the source of truth for what's installed: ``uv tool list`` reports
each tool's name and version. The installable shells out to uv for both
install and uninstall, and to ``uv tool list`` (not a local cache) for
``installed_version`` — that way a version installed from the shell
outside the worker is still reported correctly.

The install itself is driven by :class:`UvToolAcquireSession` in
``utils/acquire/uv_tool.py`` — this module only owns the installable
metadata (version listing, PyPI lookup, installed-state probe).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ...contracts import (
    AcquireSession,
    InstallState,
    InstallVersion,
    ServiceInstall,
)
from .acquire import CptrAcquireSession

_PYPI_URL = "https://pypi.org/pypi/cptr/json"
_PACKAGE_NAME = "cptr"
_BINARY_NAME = "cptr"
_UV_TIMEOUT_S = 300.0
_LIST_TIMEOUT_S = 10.0
_HTTP_TIMEOUT_S = 15.0
_USER_AGENT = "genesis-worker"


# --- helpers ---------------------------------------------------------------


def _http_get_json(url: str, *, timeout: float) -> Any:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _uv_tool_installed_version(package: str, *, timeout: float) -> str | None:
    """Parse ``uv tool list`` for ``package`` and return its version, or None.

    Output format (current uv): one line per tool ``<name> v<version>``,
    followed by an indented bullet. The first whitespace-separated token
    is the tool name, the second is the version with a leading ``v``.
    """
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == package:
            return parts[1].lstrip("v") or None
    return None


# --- installable -----------------------------------------------------------


class CptrInstall(ServiceInstall):
    """Installable for the cptr Python package via ``uv tool install``."""

    name = _PACKAGE_NAME

    def state(self) -> InstallState:
        return InstallState.INSTALLED if self.binary_path() else InstallState.NOT_INSTALLED

    def installed_version(self) -> str | None:
        """The version uv reports for this package — no local cache.

        Querying uv directly (not a state file) means a user-installed
        version via the shell is still surfaced correctly here.
        """
        return _uv_tool_installed_version(_PACKAGE_NAME, timeout=_LIST_TIMEOUT_S)

    def available_versions(self) -> list[InstallVersion]:
        """Latest version on PyPI. Single-entry list for v1 — no picker.

        URL points at the wheel we expect uv to fetch; ``sha256`` and
        ``size_bytes`` come from the PyPI JSON for the matching
        ``bdist_wheel`` so the Binaries page (if added later) can show
        an honest size.
        """
        try:
            data = _http_get_json(_PYPI_URL, timeout=_HTTP_TIMEOUT_S)
        except (urllib.error.URLError, OSError, TimeoutError):
            return []
        info = data.get("info", {})
        version = info.get("version")
        if not version:
            return []
        size: int | None = None
        sha256: str | None = None
        url = info.get("package_url") or f"https://pypi.org/project/{_PACKAGE_NAME}/{version}/"
        for u in data.get("urls", []):
            if u.get("packagetype") == "bdist_wheel":
                size = u.get("size")
                sha256 = u.get("digests", {}).get("sha256")
                url = u.get("url") or url
                break
        return [InstallVersion(version=version, url=url, sha256=sha256, size_bytes=size)]

    def binary_path(self) -> Path | None:
        found = shutil.which(_BINARY_NAME)
        return Path(found) if found else None

    def install(self, *, version: str | None = None) -> AcquireSession:
        return CptrAcquireSession(
            package_name=_PACKAGE_NAME,
            version=version,
            timeout_s=_UV_TIMEOUT_S,
        )

    def uninstall(self, *, version: str | None = None) -> None:
        try:
            subprocess.run(
                ["uv", "tool", "uninstall", _PACKAGE_NAME],
                capture_output=True,
                text=True,
                check=False,
                timeout=_UV_TIMEOUT_S,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            # uv missing or hung — nothing to clean locally anyway, since
            # we don't track state. The shell-out will surface to logs.
            pass


__all__ = ["CptrInstall"]
