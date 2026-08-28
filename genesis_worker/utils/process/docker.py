"""Docker container lifecycle — run, stop, remove, pull, logs.

Mirrors :class:`~genesis_worker.utils.process.tmux.TmuxProcess`. Container
services compose this utility instead of shelling out to ``docker`` directly.

Every subprocess call goes through :func:`subprocess.run` with
``check=False, capture_output=True, text=True``. Return codes and stderr are
parsed in one place; callers receive dataclasses or ``RuntimeError``.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from ...contracts import StartResult, StopResult

_DEFAULT_STOP_TIMEOUT_S = 30.0
_DEFAULT_PULL_TIMEOUT_S = 1800.0
_DEFAULT_INSPECT_TIMEOUT_S = 10.0
_DEFAULT_RUN_TIMEOUT_S = 60.0
_USER_AGENT = "genesis-worker"
_GHCR_BASE = "https://ghcr.io"


def _run(args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Single subprocess call. Never ``shell=True``."""
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)


def _http_get_json(url: str, *, headers: dict[str, str] | None = None,
                   timeout: float = 30.0) -> Any:
    """Fetch ``url`` and parse JSON. Headers default to UA."""
    h = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class _Canceled(Exception):
    """Raised by :meth:`DockerContainer.pull` when the cancel callback fires."""


class DockerContainer:
    """Lifecycle for one Docker container identified by ``name``.

    Instance methods operate on the configured name. Class methods probe
    the host environment (``docker_available``, ``nvidia_runtime_available``,
    image/tag inspection, GHCR tag listing, pull) and don't need an instance.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    # --- lifecycle ---------------------------------------------------------

    def is_running(self) -> bool:
        """True iff a container named ``self._name`` exists and is in ``Running`` state."""
        result = _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self._name],
            timeout=_DEFAULT_INSPECT_TIMEOUT_S,
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip().lower() == "true"

    def run(
        self,
        *,
        image: str,
        command: list[str] | None = None,
        ports: dict[str, tuple[str, int]] | None = None,
        volumes: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        runtime: str | None = None,
        gpu_flags: list[str] | None = None,
        hostname: str | None = None,
        restart: str = "unless-stopped",
        extra_args: list[str] | None = None,
    ) -> StartResult:
        """Create and start a detached container.

        ``ports`` maps container-port/protocol to ``(host_ip, host_port)`` —
        e.g. ``{"8188/tcp": ("0.0.0.0", 8188)}``. ``volumes`` maps container
        path to host path; both bind mounts are read-write. ``env`` is the
        container environment. ``runtime`` is the Docker runtime
        (``nvidia`` or ``None``). ``gpu_flags`` are the value of ``--gpus``
        when ``runtime`` is set.

        Any prior container of the same name is removed first
        (idempotent). The function returns ``StartResult(ok=True)`` only
        when ``docker run`` succeeds; callers should still call
        ``wait_ready`` for HTTP readiness (this utility does not fold
        readiness into the result).
        """
        self.remove()

        argv: list[str] = ["docker", "run", "-d", "--name", self._name, "--restart", restart]
        if hostname:
            argv += ["--hostname", hostname]
        for container_port, (host_ip, host_port) in (ports or {}).items():
            argv += ["-p", f"{host_ip}:{host_port}:{container_port}"]
        for container_path, host_path in (volumes or {}).items():
            argv += ["-v", f"{host_path}:{container_path}"]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]
        if runtime:
            argv += ["--runtime", runtime]
        if gpu_flags:
            argv += ["--gpus", ",".join(gpu_flags)]
        argv.append(image)
        if command:
            argv += list(command)
        if extra_args:
            argv += list(extra_args)

        result = _run(argv, timeout=_DEFAULT_RUN_TIMEOUT_S)
        if result.returncode != 0:
            return StartResult(
                ok=False,
                message=f"docker run failed (rc={result.returncode}): "
                f"{result.stderr.strip() or 'unknown error'}",
            )
        return StartResult(ok=True, message=f"started {self._name}")

    def stop(self, *, timeout_s: float = _DEFAULT_STOP_TIMEOUT_S) -> StopResult:
        """Stop the container via SIGTERM, falling back to SIGKILL on timeout.

        ``docker stop`` is given ``timeout_s`` seconds to drain; if the
        container is still alive afterwards, ``docker kill`` is issued.
        Either way the function returns ``StopResult(ok=True)`` when the
        container reaches a non-running state.
        """
        if not self.is_running():
            return StopResult(ok=True, message="no container")

        result = _run(
            ["docker", "stop", "--time", str(int(timeout_s)), self._name],
            timeout=timeout_s + 10.0,
        )
        if result.returncode == 0:
            return StopResult(ok=True, message=f"stopped {self._name}")

        # Graceful stop timed out — escalate to SIGKILL.
        kill_result = _run(["docker", "kill", self._name], timeout=_DEFAULT_INSPECT_TIMEOUT_S)
        if kill_result.returncode == 0:
            return StopResult(ok=True, message=f"killed {self._name} (forced)")
        return StopResult(
            ok=False,
            message=f"docker stop failed (rc={result.returncode}): "
            f"{result.stderr.strip() or 'unknown error'}",
        )

    def remove(self) -> None:
        """``docker rm -f <name>``. Idempotent: no error when absent."""
        _run(["docker", "rm", "-f", self._name], timeout=_DEFAULT_INSPECT_TIMEOUT_S)

    # --- logs --------------------------------------------------------------

    def logs(self, tail_lines: int = 200) -> str:
        """Return the last ``tail_lines`` lines of the container's logs.

        Empty string when the container does not exist or has no logs yet.
        """
        result = _run(
            ["docker", "logs", "--tail", str(int(tail_lines)), self._name],
            timeout=_DEFAULT_INSPECT_TIMEOUT_S,
        )
        if result.returncode != 0:
            return ""
        return result.stdout + result.stderr

    # --- install backend ---------------------------------------------------

    @staticmethod
    def image_present(image: str) -> bool:
        """True iff ``docker image inspect <image>`` exits 0."""
        result = _run(
            ["docker", "image", "inspect", image],
            timeout=_DEFAULT_INSPECT_TIMEOUT_S,
        )
        return result.returncode == 0

    @staticmethod
    def list_local_tags(repo: str) -> list[str]:
        """Local tags for ``repo``.

        Parses ``docker images --format '{{.Repository}}:{{.Tag}}' <repo>``.
        Tags equal to ``<repo>:latest`` are included. Empty lines are skipped.
        """
        result = _run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", repo],
            timeout=_DEFAULT_INSPECT_TIMEOUT_S,
        )
        if result.returncode != 0:
            return []
        out: list[str] = []
        prefix = f"{repo}:"
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith(prefix):
                continue
            out.append(line[len(prefix):])
        return out

    @staticmethod
    def list_remote_tags(repo: str, *, auth_token: str | None = None) -> list[str]:
        """Tags for ``repo`` from the GHCR v2 registry.

        Public packages are reachable without a token. The two-step
        GHCR flow: token endpoint → tags endpoint. Caller caches the
        result; this method does not. Returns ``[]`` on any network
        or parsing failure.
        """
        token = auth_token
        if token is None:
            token = DockerContainer._fetch_ghcr_token(repo)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{_GHCR_BASE}/v2/{repo}/tags/list"
        try:
            data = _http_get_json(url, headers=headers, timeout=30.0)
        except (urllib.error.URLError, TimeoutError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        tags = data.get("tags", [])
        return [t for t in tags if isinstance(t, str)]

    @staticmethod
    def _fetch_ghcr_token(repo: str) -> str | None:
        """Fetch an anonymous GHCR pull token scoped to ``repo``.

        Public packages work without auth; the token endpoint just
        proves the requester is allowed to pull anonymously.
        """
        url = f"{_GHCR_BASE}/token?service=ghcr.io&scope=repository:{repo}:pull"
        try:
            data = _http_get_json(url, timeout=15.0)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
        if isinstance(data, dict):
            token = data.get("token")
            if isinstance(token, str):
                return token
        return None

    @staticmethod
    def pull(
        image: str,
        *,
        progress: Callable[[str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        timeout_s: float = _DEFAULT_PULL_TIMEOUT_S,
    ) -> None:
        """Pull ``image``, streaming stderr lines to ``progress``.

        Each non-empty line is forwarded as a single ``progress(line)`` call.
        ``cancel()`` is checked between lines; when it returns True the
        pull is aborted (no clean way to cancel an in-flight ``docker
        pull`` — we let the current line finish, then raise).

        Raises :class:`RuntimeError` on non-zero exit; :class:`_Canceled`
        when cancel fires.
        """
        result = subprocess.run(
            ["docker", "pull", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        # ``docker pull`` writes progress to stderr; stdout is empty on success.
        for line in (result.stderr or "").splitlines():
            if cancel is not None and cancel():
                raise _Canceled()
            line = line.rstrip()
            if line and progress is not None:
                progress(line)

        if cancel is not None and cancel():
            raise _Canceled()

        if result.returncode != 0:
            raise RuntimeError(
                f"docker pull {image} failed (rc={result.returncode}): "
                f"{(result.stderr or '').strip()[-500:] or 'unknown error'}"
            )

    # --- environment probes ------------------------------------------------

    @staticmethod
    def docker_available() -> bool:
        """True iff the ``docker`` binary is on PATH and the daemon responds."""
        result = _run(["docker", "version"], timeout=_DEFAULT_INSPECT_TIMEOUT_S)
        return result.returncode == 0

    @staticmethod
    def nvidia_runtime_available() -> bool:
        """True iff ``docker info`` reports the nvidia runtime."""
        result = _run(["docker", "info"], timeout=_DEFAULT_INSPECT_TIMEOUT_S)
        if result.returncode != 0:
            return False
        return "nvidia" in (result.stdout or "").lower()


__all__ = ["DockerContainer"]
