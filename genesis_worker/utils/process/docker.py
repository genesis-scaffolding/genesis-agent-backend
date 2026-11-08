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
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...contracts import StartResult, StopResult

_DEFAULT_STOP_TIMEOUT_S = 30.0
_DEFAULT_PULL_TIMEOUT_S = 1800.0
_DEFAULT_INSPECT_TIMEOUT_S = 10.0
_DEFAULT_RUN_TIMEOUT_S = 60.0
_USER_AGENT = "genesis-worker"
_GHCR_BASE = "https://ghcr.io"
_GHCR_TOKEN_ATTEMPTS = 3
_GHCR_TOKEN_BACKOFF_S = 1.5

# Module-level cache for ``docker --progress=json`` support. Probed
# once on first use; older Docker daemons (pre-23.0) don't accept
# the flag and would otherwise fail every pull.
_PROGRESS_SUPPORT_CACHE: dict[str, bool] = {}


def _run(args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Single subprocess call. Never ``shell=True``."""
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    accept: str = "application/json",
) -> Any:
    """Fetch ``url`` and parse JSON. Headers default to UA."""
    h = {"User-Agent": _USER_AGENT, "Accept": accept}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class _Canceled(Exception):
    """Raised by :meth:`DockerContainer.pull` when the cancel callback fires."""


@dataclass(frozen=True)
class ContainerPortBinding:
    """One container's declared host-port bindings.

    ``host_ports`` are the host-side ports the container *declares* — the
    ``HostConfig.PortBindings`` of the container, whether it is running or
    stopped. A stopped container does not hold its port (docker tears the
    network sandbox down), but the declaration survives and a later start
    would collide with whoever took the port, so the declaration is what
    the allocator reserves.

    ``compose_project`` is the ``com.docker.compose.project`` label compose
    puts on every container of a stack — that is how a compose service
    recognises its own containers.

    ``running`` says whether the container is up right now. A stopped
    container declares its bindings but holds none of them, so only a
    running container's port can be adopted (ADR-045).

    ``port_map`` pairs the container port with the host port it is
    published on — ``((8081, 8098),)`` for a container that publishes
    ``8081/tcp`` on host port 8098. The container port is the half the
    allocator's callers need to map a service's use site to a binding.
    """

    name: str
    host_ports: tuple[int, ...]
    compose_project: str | None = None
    running: bool = False
    port_map: tuple[tuple[int, int], ...] = ()


def container_port_bindings() -> list[ContainerPortBinding]:
    """Every container on the node, with its declared host-port bindings.

    Empty list when docker is absent or the daemon is unreachable — the
    caller treats that as "no container claims a port", which is exactly as
    good as the bind test on its own.
    """
    try:
        listed = _run(["docker", "ps", "-aq"], timeout=_DEFAULT_INSPECT_TIMEOUT_S)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if listed.returncode != 0:
        return []
    ids = [line.strip() for line in (listed.stdout or "").splitlines() if line.strip()]
    bindings: list[ContainerPortBinding] = []
    for container_id in ids:
        try:
            inspected = _run(
                ["docker", "inspect", container_id], timeout=_DEFAULT_INSPECT_TIMEOUT_S
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if inspected.returncode != 0:
            continue
        try:
            payload = json.loads(inspected.stdout or "")
        except json.JSONDecodeError:
            continue
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if isinstance(entry, dict):
                bindings.append(_binding_from_inspect(entry))
    return bindings


def _binding_from_inspect(entry: dict) -> ContainerPortBinding:
    """Read one ``docker inspect`` entry into a :class:`ContainerPortBinding`."""
    name = str(entry.get("Name") or "").lstrip("/")
    host_config = entry.get("HostConfig") or {}
    port_bindings = host_config.get("PortBindings") or {}
    ports: set[int] = set()
    port_map: dict[int, int] = {}
    if isinstance(port_bindings, dict):
        for container_port, entries in port_bindings.items():
            container = str(container_port).split("/")[0]
            if not container.isdigit():
                continue
            for binding in entries or []:
                if not isinstance(binding, dict):
                    continue
                raw = binding.get("HostPort")
                if raw in (None, ""):
                    continue
                try:
                    host_port = int(raw)
                except (TypeError, ValueError):
                    continue
                ports.add(host_port)
                # A container port published on several addresses keeps the
                # first binding — one host port per container port.
                port_map.setdefault(int(container), host_port)
    config = entry.get("Config") or {}
    labels = config.get("Labels") or {} if isinstance(config, dict) else {}
    project = labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
    state = entry.get("State") or {}
    running = bool(state.get("Running")) if isinstance(state, dict) else False
    return ContainerPortBinding(
        name=name,
        host_ports=tuple(sorted(ports)),
        compose_project=str(project) if project else None,
        running=running,
        port_map=tuple(port_map.items()),
    )


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
        """True iff a container named ``self._name`` exists and is in ``Running`` state.

        Returns ``False`` on a missing docker binary, daemon unreachable,
        or any other failure — these are not error states for the caller,
        just "the container isn't running here".
        """
        try:
            result = _run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self._name],
                timeout=_DEFAULT_INSPECT_TIMEOUT_S,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
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
        devices: list[str] | None = None,
        group_add: str | None = None,
        hostname: str | None = None,
        restart: str = "unless-stopped",
        shm_size: str | None = None,
        extra_args: list[str] | None = None,
        security_opts: list[str] | None = None,
    ) -> StartResult:
        """Create and start a detached container.

        ``ports`` maps container-port/protocol to ``(host_ip, host_port)`` —
        e.g. ``{"8188/tcp": ("0.0.0.0", 8188)}``. ``volumes`` maps container
        path to host path; both bind mounts are read-write. ``env`` is the
        container environment. ``runtime`` is the Docker runtime
        (``nvidia`` or ``None``). ``gpu_flags`` are the value of ``--gpus``
        when ``runtime`` is set. ``shm_size`` adds ``--shm-size`` when set
        (e.g. ``"1g"`` for browser-backed services that need more than the
        64 MB default). ``devices`` adds ``--device`` flags for direct
        host-device passthrough (e.g. ROCm's ``/dev/kfd``, ``/dev/dri``).
        ``group_add`` adds ``--group-add`` (e.g. ``video`` for
        ``/dev/dri`` render nodes). ``security_opts`` adds
        ``--security-opt`` flags (e.g. ``seccomp=unconfined``,
        ``apparmor=unconfined``).

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
        for device in devices or []:
            argv += ["--device", device]
        if group_add:
            argv += ["--group-add", group_add]
        if shm_size:
            argv += ["--shm-size", shm_size]
        if security_opts:
            for opt in security_opts:
                argv += ["--security-opt", opt]
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
    def supports_json_progress() -> bool:
        """True if this ``docker`` binary accepts ``--progress=json``.

        Some distributions (notably our ``moby`` build) omit the
        ``--progress`` flag even at high version numbers. We probe
        ``docker pull --help`` and look for the flag in the option
        list rather than relying on the version number. Probed once
        and cached.
        """
        cached = _PROGRESS_SUPPORT_CACHE.get("json")
        if cached is not None:
            return cached
        try:
            result = _run(
                ["docker", "pull", "--help"],
                timeout=_DEFAULT_INSPECT_TIMEOUT_S,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            _PROGRESS_SUPPORT_CACHE["json"] = False
            return False
        if result.returncode != 0:
            _PROGRESS_SUPPORT_CACHE["json"] = False
            return False
        # ``--progress`` is a real flag iff the help text mentions it.
        # ``podman`` and other build-flavours may report a similar
        # version but not include the flag, so we check the actual
        # surface.
        supported = "--progress" in (result.stdout or "")
        _PROGRESS_SUPPORT_CACHE["json"] = supported
        return supported

    @staticmethod
    def image_present(image: str) -> bool:
        """True iff ``docker image inspect <image>`` exits 0.

        Returns ``False`` on a missing docker binary, daemon unreachable,
        or any other failure — the caller treats "image absent" and
        "docker absent" the same way: not installed.
        """
        try:
            result = _run(
                ["docker", "image", "inspect", image],
                timeout=_DEFAULT_INSPECT_TIMEOUT_S,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
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
            out.append(line[len(prefix) :])
        return out

    @staticmethod
    def list_remote_tags(repo: str) -> list[str]:
        """Tags for ``repo`` from its registry, newest-first where known.

        Dispatches on the registry prefix: GHCR repos use the v2
        token → tags flow; everything else (Docker Hub and other
        registries) uses the library/namespace v2 catalog. Both
        endpoints are public for public images. Returns ``[]`` on any
        network or parsing failure — the caller treats that as
        "registry unreachable", not an error state.

        Caller caches the result; this method does not.
        """
        if repo.startswith("ghcr.io/"):
            return DockerContainer._ghcr_tags(repo)
        return DockerContainer._hub_tags(repo)

    @staticmethod
    def _ghcr_tags(repo: str) -> list[str]:
        """GHCR v2 flow: anonymous token endpoint → tags endpoint."""
        name = repo.removeprefix("ghcr.io/").strip("/")
        token = DockerContainer._fetch_ghcr_token(name)
        headers = {"Authorization": f"Bearer {token}"} if token else None
        url = f"{_GHCR_BASE}/v2/{name}/tags/list"
        try:
            data = _http_get_json(url, headers=headers, timeout=30.0)
        except (urllib.error.URLError, TimeoutError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        tags = data.get("tags", [])
        return [t for t in tags if isinstance(t, str)]

    @staticmethod
    def _hub_tags(repo: str) -> list[str]:
        """Docker Hub v2 catalog: ``/v2/<name>/tags/list?n=<max>``.

        Anonymous token from ``auth.docker.io`` (public images are
        pullable without credentials — the token just authorises the
        read), then the tags endpoint. Hub lists tags alphabetically
        (oldest first), so the result is reversed to newest-first —
        the order installables display in their pickers. 429s under
        heavy load surface as ``[]`` like any other failure.
        """
        # ``docker.io/org/img`` is the canonical spelling of ``org/img``;
        # strip it. A leading path component with a dot or colon is a
        # registry host (``registry.example.com/org/img``,
        # ``localhost:5000/img``) — query that host instead of the hub.
        repo = repo.removeprefix("docker.io/")
        repo = repo.rstrip("/")
        host = ""
        if "/" in repo:
            first = repo.split("/", 1)[0]
            if "." in first or ":" in first:
                host, repo = first, repo.split("/", 1)[1]
        if not host and "/" not in repo:
            repo = f"library/{repo}"  # bare names are official library images
        if host:
            # Non-hub registries don't share the auth.docker.io flow;
            # best-effort unauthenticated (most public mirrors allow it).
            token = None
            base = f"https://{host}"
        else:
            token = DockerContainer._fetch_hub_token(repo)
            base = "https://registry-1.docker.io"
        headers = {"Authorization": f"Bearer {token}"} if token else None
        url = f"{base}/v2/{repo}/tags/list?n=1000"
        try:
            data = _http_get_json(url, headers=headers, timeout=30.0, accept="*")
        except (urllib.error.URLError, TimeoutError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        tags = data.get("tags")
        if tags is None:
            return []
        return [t for t in reversed(tags) if isinstance(t, str)]

    @staticmethod
    def _fetch_hub_token(repo: str) -> str | None:
        """Anonymous Docker Hub pull token scoped to ``repo``."""
        url = (
            f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
        )
        try:
            data = _http_get_json(url, timeout=15.0, accept="*")
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
        if isinstance(data, dict):
            token = data.get("token")
            if isinstance(token, str):
                return token
        return None

    @staticmethod
    def _fetch_ghcr_token(repo: str) -> str | None:
        """Fetch an anonymous GHCR pull token scoped to ``repo``.

        Public packages work without auth; the token endpoint just
        proves the requester is allowed to pull anonymously. That
        endpoint throttles by IP and stalls the connection instead of
        returning 429, so one attempt is not enough.
        """
        url = f"{_GHCR_BASE}/token?service=ghcr.io&scope=repository:{repo}:pull"
        for attempt in range(_GHCR_TOKEN_ATTEMPTS):
            if attempt:
                time.sleep(_GHCR_TOKEN_BACKOFF_S * attempt)
            try:
                data = _http_get_json(url, timeout=15.0, accept="application/json")
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
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
        progress_format: str = "json",
    ) -> None:
        """Pull ``image``, streaming stderr lines to ``progress``.

        ``progress_format="json"`` requests ``--progress=json`` from
        docker (Docker 23.0+). When the daemon is older and rejects
        the flag, we silently fall back to plain text — the parser
        ignores non-JSON lines, so the pull still completes; the UI
        just won't get byte-level progress.

        ``progress_format="plain"`` never adds the flag.

        Each non-empty line is forwarded as a single ``progress(line)`` call.
        ``cancel()`` is checked between lines; when it returns True the
        pull is aborted (no clean way to cancel an in-flight ``docker
        pull`` — we let the current line finish, then raise).

        Raises :class:`RuntimeError` on non-zero exit; :class:`_Canceled`
        when cancel fires.
        """
        argv = ["docker", "pull"]
        if progress_format == "json" and DockerContainer.supports_json_progress():
            argv.append("--progress=json")
        argv.append(image)
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        # ``docker pull`` writes progress to either stdout or stderr
        # depending on the daemon build and progress format. Capture
        # both, then forward any non-empty line to the caller's
        # ``progress`` callback. Skip empty lines.
        merged = ((result.stdout or "") + "\n" + (result.stderr or "")).splitlines()
        for line in merged:
            if cancel is not None and cancel():
                raise _Canceled()
            line = line.rstrip()
            if line and progress is not None:
                progress(line)

        if cancel is not None and cancel():
            raise _Canceled()

        if result.returncode != 0:
            err_blob = (result.stderr or result.stdout or "").strip()[-500:]
            raise RuntimeError(
                f"docker pull {image} failed (rc={result.returncode}): "
                f"{err_blob or 'unknown error'}"
            )

    # --- exec ---------------------------------------------------------------

    def exec_run(
        self,
        cmd: list[str],
        *,
        timeout_s: float = 10.0,
    ) -> tuple[int, str, str]:
        """Run ``cmd`` inside the container via ``docker exec``.

        Returns ``(returncode, stdout, stderr)``. Empty stdout/stderr on
        any failure (container not running, exec error, etc.).
        """
        argv = ["docker", "exec", self._name, *cmd]
        try:
            result = _run(argv, timeout=timeout_s)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return -1, "", ""
        return result.returncode, result.stdout or "", result.stderr or ""

    # --- environment probes ------------------------------------------------

    @staticmethod
    def docker_available() -> bool:
        """True iff the ``docker`` binary is on PATH and the daemon responds."""
        result = _run(["docker", "version"], timeout=_DEFAULT_INSPECT_TIMEOUT_S)
        return result.returncode == 0

    @staticmethod
    def nvidia_runtime_available() -> bool:
        """True iff ``docker info`` registers the legacy ``nvidia`` OCI runtime.

        Token-matches the ``Runtimes:`` line — see
        :func:`genesis_worker.utils.collectors.hardware._nvidia_runtime_available`
        for the rationale. No production callers today; the framework-level
        probe is the source of truth.
        """
        result = _run(["docker", "info"], timeout=_DEFAULT_INSPECT_TIMEOUT_S)
        if result.returncode != 0:
            return False
        for line in (result.stdout or "").splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("runtimes:"):
                return "nvidia" in stripped.split()[1:]
        return False


__all__ = ["ContainerPortBinding", "DockerContainer", "container_port_bindings"]
