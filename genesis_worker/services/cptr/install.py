"""Installable for cptr — driven by ``uv tool install`` rather than a tarball download.

uv is the source of truth for what's installed: ``uv tool list`` reports
each tool's name and version. The installable shells out to uv for both
install and uninstall, and to ``uv tool list`` (not a local cache) for
``installed_version`` — that way a version installed from the shell
outside the worker is still reported correctly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...contracts import (
    AcquireChoice,
    AcquireStep,
    InstallSession,
    InstallState,
    InstallVersion,
    ServiceInstall,
)

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


# --- session ---------------------------------------------------------------


@dataclass
class _SessionState:
    step: AcquireStep
    canceled: bool = False
    done: bool = False


class _UvToolInstallSession(InstallSession):
    """Streaming install session backed by ``uv tool install``."""

    def __init__(self, *, package_name: str, version: str | None) -> None:
        self._package_name = package_name
        self._version = version
        spec = f"{package_name}=={version}" if version else f"{package_name}@latest"
        self._state = _SessionState(
            step=AcquireStep(kind="fetching", title=f"installing {spec}")
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def current_step(self) -> AcquireStep:
        return self._state.step

    def submit(self, choice: AcquireChoice) -> AcquireStep:
        return self._state.step

    def cancel(self) -> None:
        self._state.canceled = True

    def wait(self) -> AcquireStep:
        self._thread.join()
        return self._state.step

    def _publish(self, step: AcquireStep) -> None:
        self._state.step = step

    def _run(self) -> None:
        try:
            self._run_inner()
        except Exception as exc:  # noqa: BLE001 — background supervisor: any failure becomes a 'failed' step
            self._publish(
                AcquireStep(
                    kind="failed",
                    title=f"install failed: {exc}",
                    error=str(exc),
                )
            )
        finally:
            self._state.done = True

    def _run_inner(self) -> None:
        spec = f"{self._package_name}=={self._version}" if self._version else f"{self._package_name}@latest"
        self._publish(AcquireStep(kind="fetching", title=f"running uv tool install {spec}"))
        if self._state.canceled:
            self._publish(AcquireStep(kind="cancelled", title="cancelled"))
            return

        try:
            result = subprocess.run(
                ["uv", "tool", "install", spec],
                capture_output=True,
                text=True,
                check=False,
                timeout=_UV_TIMEOUT_S,
            )
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(f"uv not found on PATH: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"uv tool install timed out after {_UV_TIMEOUT_S:.0f}s") from exc

        if self._state.canceled:
            self._publish(AcquireStep(kind="cancelled", title="cancelled"))
            return
        if result.returncode != 0:
            raise RuntimeError(
                f"uv tool install failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip() or 'no output'}"
            )

        if shutil.which(self._package_name) is None:
            raise RuntimeError(
                f"{self._package_name} binary not on PATH after install — "
                "is ~/.local/bin on PATH?"
            )

        self._publish(
            AcquireStep(
                kind="complete",
                title=f"installed {spec}",
            )
        )


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

    def install(self, *, version: str | None = None) -> InstallSession:
        return _UvToolInstallSession(package_name=_PACKAGE_NAME, version=version)

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