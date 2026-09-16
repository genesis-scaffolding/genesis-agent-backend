"""uv-tool-backed ``DeclarativeServiceBase`` subclass.

Lifecycle uses ``TmuxProcess`` + ``HealthProbe``; install is a
``uv tool install`` round-trip via ``UvToolAcquireSession``. The cptr
service is the first adopter (phase 1's proof); future peers plug in
by building a ``UvServiceConfig`` and pointing ``_post_install`` at
their own post-install patches.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...contracts import (
    AcquireSession,
    InstallState,
    InstallVersion,
    ServiceCapabilities,
    ServiceCategory,
    ServiceContext,
    ServiceInstall,
    ServiceResourceEstimate,
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
)
from ..acquire import UvToolAcquireSession
from ..net import HealthProbe
from ..process import TmuxProcess
from .base import DeclarativeServiceBase, DeclarativeServiceConfig

_PYPI_TIMEOUT_S = 15.0
_USER_AGENT = "genesis-worker"
_UV_LIST_TIMEOUT_S = 10.0
_UV_INSTALL_TIMEOUT_S = 300.0


def _http_get_json(url: str, *, timeout: float) -> Any:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _uv_tool_installed_version(package: str, *, timeout: float) -> str | None:
    """Parse ``uv tool list`` for ``package`` and return its version, or None."""
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


@dataclass(frozen=True)
class UvServiceConfig(DeclarativeServiceConfig):
    """Per-service uv-tool config. Set identity, capabilities, options_model in caller."""

    category: ServiceCategory = ServiceCategory.OTHER
    package_name: str = ""  # `uv tool install <package_name>`
    binary_name: str = ""  # on PATH after install
    command: list[str] = field(default_factory=list)  # args after the binary
    install_env: dict[str, str] = field(default_factory=dict)
    command_env: dict[str, str] = field(default_factory=dict)

    listen_host: str = "0.0.0.0"
    listen_port: int = 0
    public_host: str | None = None
    health_probe_path: str = "/"
    health_timeout_s: float = 60.0
    session_name: str | None = None  # defaults to name
    graceful_stop_timeout_s: float = 10.0


class UvToolInstall(ServiceInstall):
    """Installable for a uv-tool-managed binary.

    Drives ``UvToolAcquireSession`` for ``install``. ``state`` /
    ``installed_version`` consult ``uv tool list`` so a user-installed
    version via the shell is still reported correctly here.
    """

    def __init__(
        self,
        *,
        package_name: str,
        binary_name: str,
        pypi_url: str | None = None,
        install_timeout_s: float = _UV_INSTALL_TIMEOUT_S,
        list_timeout_s: float = _UV_LIST_TIMEOUT_S,
    ) -> None:
        self._package_name = package_name
        self._binary_name = binary_name
        self._pypi_url = pypi_url or f"https://pypi.org/pypi/{package_name}/json"
        self._install_timeout_s = install_timeout_s
        self._list_timeout_s = list_timeout_s
        self.name = package_name

    def state(self) -> InstallState:
        return InstallState.INSTALLED if self.binary_path() else InstallState.NOT_INSTALLED

    def binary_path(self) -> Path | None:
        found = shutil.which(self._binary_name)
        return Path(found) if found else None

    def installed_version(self) -> str | None:
        return _uv_tool_installed_version(self._package_name, timeout=self._list_timeout_s)

    def available_versions(self) -> list[InstallVersion]:
        """Latest version on PyPI. Single-entry list for v1 — no picker.

        ``sha256`` and ``size_bytes`` come from the PyPI JSON for the
        matching ``bdist_wheel`` so future Binaries-style UI can show
        an honest size.
        """
        try:
            data = _http_get_json(self._pypi_url, timeout=_PYPI_TIMEOUT_S)
        except (urllib.error.URLError, OSError, TimeoutError):
            return []
        info = data.get("info", {})
        version = info.get("version")
        if not version:
            return []
        size: int | None = None
        sha256: str | None = None
        url = info.get("package_url") or f"https://pypi.org/project/{self._package_name}/{version}/"
        for u in data.get("urls", []):
            if u.get("packagetype") == "bdist_wheel":
                size = u.get("size")
                sha256 = u.get("digests", {}).get("sha256")
                url = u.get("url") or url
                break
        return [InstallVersion(version=version, url=url, sha256=sha256, size_bytes=size)]

    def install(self, *, version: str | None = None) -> AcquireSession:
        return UvToolAcquireSession(
            package_name=self._package_name,
            version=version,
            timeout_s=self._install_timeout_s,
        )

    def uninstall(self, *, version: str | None = None) -> None:
        try:
            subprocess.run(
                ["uv", "tool", "uninstall", self._package_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._install_timeout_s,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass


class UvService(DeclarativeServiceBase[UvServiceConfig]):
    """Declarative service that runs a uv-tool-installed binary in a tmux session.

    Lifecycle uses ``TmuxProcess`` + ``HealthProbe``. Subclasses can
    override ``_post_install()`` to apply per-service patches (cptr's
    pi-agent timeout is the canonical example).
    """

    def __init__(self, ctx: ServiceContext, *, config: UvServiceConfig) -> None:
        super().__init__(ctx, config=config)
        self._session_name = config.session_name or config.name
        self._install_obj = UvToolInstall(
            package_name=config.package_name,
            binary_name=config.binary_name,
        )

    # --- introspection ----------------------------------------------------

    @property
    def listen_address(self) -> str:
        return f"{self.config.listen_host}:{self.config.listen_port}"

    @property
    def installed_version(self) -> str | None:
        """The version uv currently reports for this package."""
        return self._install_obj.installed_version()

    # --- contract overrides -----------------------------------------------

    def capabilities(self) -> ServiceCapabilities:
        return self.config.capabilities

    def resource_estimate(self) -> ServiceResourceEstimate:
        return self.config.resource_estimate

    @property
    def category(self) -> ServiceCategory:
        return self.config.category

    @property
    def description(self) -> str:
        return self.config.description

    def is_available(self) -> bool:
        return self._install_obj.binary_path() is not None

    def is_running(self) -> bool:
        return TmuxProcess(self._session_name).exists()

    def runtime_endpoint(self) -> str | None:
        return None

    def web_ui_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        return f"http://{self.public_host()}:{self.config.listen_port}/"

    def tail_log(self, n_bytes: int = 8192) -> str:
        """Return the last ``n_bytes`` of the log file, or "" if missing.

        The lifecycle pipes the process's stdout/stderr into the log
        via ``tee -a``, so this is the canonical source for the process
        console output.
        """
        path = self.log_file
        if not path.is_file():
            return ""
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            return f.read().decode("utf-8", errors="replace")

    def start(self) -> StartResult:
        binary = self._install_obj.binary_path()
        if binary is None:
            return StartResult(ok=False, message=f"{self.config.binary_name} binary not installed")
        # Build command from binary + configured command args.
        cmd = " ".join(
            [shlex.quote(str(binary)), *(shlex.quote(str(c)) for c in self.config.command)]
        )
        # Allow subclass hooks (e.g. cptr's pi-agent patch) to run pre-start.
        self._post_install()
        tmux = TmuxProcess(self._session_name)
        result = tmux.start(cmd, self.log_file)
        if not result.ok:
            return result
        if self.wait_ready(self.config.health_timeout_s):
            return StartResult(ok=True, message=f"started {self._session_name}")
        return StartResult(
            ok=False,
            message=f"did not become ready in {self.config.health_timeout_s:.0f}s; see {self.log_file}",
        )

    def _post_install(self) -> None:
        """Hook subclasses override for per-service post-install patches.

        Runs at the start of every ``start()`` (so the patch is applied
        even when a user manually re-runs the binary via the shell and
        then returns to the dashboard). Default: no-op.
        """

    def stop(self) -> StopResult:
        tmux = TmuxProcess(self._session_name)
        if not tmux.exists():
            return StopResult(ok=True, message="no session")
        tmux.send_interrupt()
        deadline = time.monotonic() + self.config.graceful_stop_timeout_s
        while time.monotonic() < deadline:
            if not tmux.exists():
                return StopResult(ok=True, message=f"killed {self._session_name}")
            time.sleep(0.5)
        if tmux.exists():
            tmux.kill()
        return StopResult(ok=True, message=f"killed {self._session_name} (forced)")

    def status(self) -> ServiceStatus:
        endpoint = (
            f"http://{HealthProbe.resolve_connect_host(self.config.listen_host)}"
            f":{self.config.listen_port}/"
        )
        if not TmuxProcess(self._session_name).exists():
            return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
        if HealthProbe(
            self.config.listen_host,
            self.config.listen_port,
            probe_path=self.config.health_probe_path,
        ).probe():
            return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
        return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)

    def wait_ready(self, timeout_s: float) -> bool:
        return HealthProbe(
            self.config.listen_host,
            self.config.listen_port,
            probe_path=self.config.health_probe_path,
        ).wait_ready(timeout_s)

    # --- install axis -----------------------------------------------------

    def _installs(self) -> list[ServiceInstall]:
        return [self._install_obj]


__all__ = [
    "UvService",
    "UvServiceConfig",
    "UvToolInstall",
]
