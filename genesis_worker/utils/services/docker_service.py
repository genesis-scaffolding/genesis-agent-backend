"""Docker-backed ``DeclarativeServiceBase`` subclass.

Lifts the structural plumbing out of the per-service Docker
implementations (``services/crawl4ai``, ``services/sillytavern``).
Phase 3 will replace those with YAML specs that build
``DockerServiceConfig`` instances; phase 1 just ships the concrete
class and the unified installable.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
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
from ..acquire import DockerPullAcquireSession
from ..net import HealthProbe
from ..process import DockerContainer
from .base import DeclarativeServiceBase, DeclarativeServiceConfig

_RELEASE_CACHE_TTL_S = 15 * 60  # same TTL as GithubReleaseTarball.


@dataclass(frozen=True)
class AuthConfig:
    """Optional API-token plumbing for docker services.

    ``token_file`` is resolved against ``ctx.state_dir`` at instance
    time. ``token_generator`` is a registered name from
    ``utils/services/hooks.py`` (phase 2); phase 1 only ships
    ``random_hex_32`` and ``random_urlsafe_32``.
    """

    enabled_option: str | None  # option name; truthy → render "JWT enabled"
    token_env_var: str  # injected as env var in start()
    token_file: Path  # resolved against ctx.state_dir at construction
    token_file_mode: int
    token_generator: str
    fallback_option: str | None = None  # explicit option wins over the file


@dataclass(frozen=True)
class DockerServiceConfig(DeclarativeServiceConfig):
    """Per-service docker config. Set identity, capabilities, options_model in caller."""

    category: ServiceCategory = ServiceCategory.OTHER
    # Image
    image_repo: str = ""
    image_tag: str = ""
    image_install_name: str = ""  # shown on the "Image" page
    source_url: str | None = None
    # Container
    container_name: str = ""
    listen_host: str = "0.0.0.0"
    listen_port: int = 0
    internal_port: int | None = None  # defaults to listen_port
    public_host: str | None = None
    web_ui_path: str = "/"  # appended to listen address for the Web UI button
    restart_policy: str = "unless-stopped"
    shm_size: str | None = None
    health_probe_path: str = "/"
    puid_default: bool = True
    pgid_default: bool = True
    # GPU
    runtime: str | None = None
    gpu_flags: list[str] | None = None
    # Bind mounts, env, args
    extra_env: dict[str, Any] = field(default_factory=dict)
    extra_volumes: dict[str, Any] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    # Data / log layout
    data_dir_subpath: str | None = "data"
    # Pre-start hooks: list of ``{"kind": ..., ...}`` dicts dispatched
    # by ``utils.services.hooks.run`` at start time. The framework keeps
    # the entries opaque (no per-kind schema here) so adding a hook
    # kind is a one-place change in ``hooks.py``.
    pre_start_hooks: tuple[dict, ...] = ()
    # Auth
    auth: AuthConfig | None = None


# --- image install ----------------------------------------------------------


def _cache_path(cache_root: Path, repo: str) -> Path:
    safe = repo.replace("/", "_")
    return cache_root / "releases-cache" / f"{safe}.json"


def _read_cache(path: Path, ttl_s: int) -> list[str] | None:
    if not path.is_file():
        return None
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    fetched_at = data.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    if (time.time() - fetched_at) >= ttl_s:
        return None
    tags = data.get("tags")
    if not isinstance(tags, list):
        return None
    return [t for t in tags if isinstance(t, str)]


def _write_cache(path: Path, tags: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    payload = {"version": 1, "fetched_at": time.time(), "tags": tags}
    with tmp.open("w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


class DockerImageInstall(ServiceInstall):
    """Installable for one Docker image — replaces the per-service duplicates.

    Single-arch (crawl4ai, sillytavern) and arch-filtered (comfyui) use
    cases share this class. ``arch_filter`` is an optional callable that
    filters remote tags — ``None`` for arch-agnostic images.
    """

    def __init__(
        self,
        *,
        image_repo: str,
        image_tag: str,
        cache_dir: Path,
        state_dir: Path,
        name: str,
        source_url: str | None,
        arch_filter=None,
    ) -> None:
        self._image_repo = image_repo
        self._image_tag = image_tag
        self._cache_dir = cache_dir
        self._state_dir = state_dir
        self._selection_path = state_dir / "current"
        self._source_url = source_url
        self._arch_filter = arch_filter
        # Plugin contract: name is a class attribute; subclasses must
        # override or pass via constructor (we do the latter).
        self.name = name

    @property
    def image_ref(self) -> str:
        return f"{self._image_repo}:{self._image_tag}"

    def source_url(self) -> str | None:
        return self._source_url

    def state(self) -> InstallState:
        return (
            InstallState.INSTALLED
            if DockerContainer.image_present(self.image_ref)
            else InstallState.NOT_INSTALLED
        )

    def installed_version(self) -> str | None:
        return self._selection_path.read_text().strip() if self._selection_path.is_file() else None

    def available_versions(self) -> list[InstallVersion]:
        """All tags reachable from the registry, newest-first.

        15-min on-disk cache mirrors ``GithubReleaseTarball.available_versions``.
        Optional ``arch_filter`` narrows the tag set (comfyui's arch pinning).
        """
        cache = _cache_path(self._cache_dir, self._image_repo)
        cached = _read_cache(cache, _RELEASE_CACHE_TTL_S)
        if cached is not None:
            tags = cached
        else:
            tags = DockerContainer.list_remote_tags(self._image_repo)
            _write_cache(cache, tags)
        if self._arch_filter is not None:
            tags = [t for t in tags if self._arch_filter(t)]
        return [
            InstallVersion(
                version=tag,
                url=f"{self._image_repo}:{tag}",
                sha256=None,
                size_bytes=None,
            )
            for tag in tags
        ]

    def invalidate_versions_cache(self) -> None:
        cache = _cache_path(self._cache_dir, self._image_repo)
        if cache.exists():
            cache.unlink()

    def binary_path(self) -> Path | None:
        return None

    def install(self, *, version: str | None = None) -> AcquireSession:
        target_tag = version or self._image_tag
        return DockerPullAcquireSession(
            image=f"{self._image_repo}:{target_tag}",
            on_complete=lambda: self._record_selection(target_tag),
        )

    def _record_selection(self, tag: str) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._selection_path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        with tmp.open("w") as f:
            f.write(tag)
        os.replace(tmp, self._selection_path)

    def uninstall(self, *, version: str | None = None) -> None:
        target = version or self.installed_version()
        if target is None:
            return
        subprocess.run(
            ["docker", "rmi", f"{self._image_repo}:{target}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
        if target == self.installed_version() and self._selection_path.is_file():
            self._selection_path.unlink()


# --- service ---------------------------------------------------------------


class DockerService(DeclarativeServiceBase[DockerServiceConfig]):
    """Declarative service that runs as a Docker container.

    Lifecycle delegates to ``DockerContainer``. Auth (the crawl4ai-style
    API token) is read/written via the optional ``AuthConfig``.
    """

    def __init__(self, ctx: ServiceContext, *, config: DockerServiceConfig) -> None:
        super().__init__(ctx, config=config)
        # Resolve the data dir from ctx.data_dir, scoped by ``data_dir_subpath``.
        if config.data_dir_subpath:
            self._data_dir = ctx.data_dir / config.data_dir_subpath
        else:
            self._data_dir = ctx.data_dir
        # Auth token path is resolved against ctx.state_dir.
        if config.auth is not None:
            self._auth_token_path = config.auth.token_file
        else:
            self._auth_token_path = None
        # Default PUID/PGID mirrors the existing crawl4ai/sillytavern behaviour.
        self._puid = os.getuid() if config.puid_default else None
        self._pgid = os.getgid() if config.pgid_default else None
        # Construct the installable once.
        self._install_obj = DockerImageInstall(
            image_repo=config.image_repo,
            image_tag=config.image_tag,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            name=config.image_install_name or config.name,
            source_url=config.source_url,
        )

    # --- introspection ----------------------------------------------------

    @property
    def image_ref(self) -> str:
        return f"{self.config.image_repo}:{self.config.image_tag}"

    @property
    def container_name(self) -> str:
        return self.config.container_name

    @property
    def listen_address(self) -> str:
        return f"{self.config.listen_host}:{self.config.listen_port}"

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def ui_panels(self) -> tuple[str, ...]:
        """Panel kinds the default status page renders for this service.

        Docker-specific default: ``(service_info, container_info,
        log_tail)``. The base class merges the YAML's ``status_panels``
        in additively on top of this. ``service_info`` carries the
        install / start / stop controls and is always first.
        """
        base = ("service_info", "container_info", "log_tail")
        extras = tuple(p for p in self.config.ui_pages if p not in base)
        return base + extras

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
        return self._install_obj.state() == InstallState.INSTALLED

    def is_running(self) -> bool:
        return DockerContainer(self.config.container_name).is_running()

    def runtime_endpoint(self) -> str | None:
        return None

    def web_ui_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        path = self.config.web_ui_path or "/"
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/"):
            path = path + "/"
        return f"http://{self.public_host()}:{self.config.listen_port}{path}"

    def tail_log(self, n_bytes: int = 8192) -> str:
        # Per the ADR, ``tail_lines`` is a sensible conversion of bytes-to-lines.
        tail_lines = max(50, n_bytes // 80)
        return DockerContainer(self.config.container_name).logs(tail_lines=tail_lines)

    def start(self) -> StartResult:
        if not self.is_available():
            return StartResult(ok=False, message=f"image not pulled: {self.image_ref}")

        # Pre-start hooks fire before env composition so their side effects
        # (e.g. generated token files) are visible to the env-builder below.
        # The hook list comes from the YAML (or, in Python subclasses, the
        # config) -- the framework never interprets the entries.
        if self.config.pre_start_hooks:
            from .hooks import PreStartHookContext
            from .hooks import run as run_hooks

            run_hooks(
                list(self.config.pre_start_hooks),
                PreStartHookContext(
                    service=self,
                    state_dir=self._ctx.state_dir,
                    data_dir=self._data_dir,
                ),
            )

        container = DockerContainer(self.config.container_name)
        internal_port = self.config.internal_port or self.config.listen_port
        ports = {f"{internal_port}/tcp": (self.config.listen_host, self.config.listen_port)}

        volumes = {
            container_path: str(host) for container_path, host in self.config.extra_volumes.items()
        }
        env = dict(self.config.extra_env)
        if self._puid is not None:
            env["PUID"] = str(self._puid)
        if self._pgid is not None:
            env["PGID"] = str(self._pgid)

        # Auth plumbing — token (or "JWT enabled" hint) injected as env var.
        if self.config.auth is not None:
            enabled_opt = self.config.auth.enabled_option
            if enabled_opt and getattr(self.options, enabled_opt, False):
                env[f"{self.config.auth.token_env_var}_JWT_ENABLED"] = "true"
            else:
                env[self.config.auth.token_env_var] = self._resolve_or_generate_token()

        return container.run(
            image=self.image_ref,
            command=self.config.extra_args or None,
            ports=ports,
            volumes=volumes,
            env=env,
            runtime=self.config.runtime,
            gpu_flags=self.config.gpu_flags,
            hostname=self.config.container_name,
            restart=self.config.restart_policy,
            shm_size=self.config.shm_size,
        )

    def _resolve_or_generate_token(self) -> str:
        """Resolve the auth token to inject — file → generate.

        Explicit option (``fallback_option``) wins outright. Otherwise the
        on-disk file is read; if absent, a 256-bit token is generated
        and persisted atomically with the configured mode.
        """
        existing = self.auth_token()
        if existing:
            return existing
        if self._auth_token_path is None:
            return ""
        auth = self.config.auth
        assert auth is not None  # narrow type for type-checkers
        token = secrets.token_hex(32)  # 64 hex chars, 256 bits.
        self._auth_token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._auth_token_path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        with tmp.open("w") as f:
            f.write(token)
        tmp.chmod(auth.token_file_mode)
        os.replace(tmp, self._auth_token_path)
        return token

    def auth_token(self) -> str | None:
        """The auth token currently in use, or ``None``.

        Reads ``fallback_option`` first, then the on-disk file. Returns
        ``None`` when ``enabled_option`` is truthy in the options (token
        is irrelevant in that mode).
        """
        if self.config.auth is None:
            return None
        auth = self.config.auth
        enabled = bool(auth.enabled_option and getattr(self.options, auth.enabled_option, False))
        if enabled:
            return None
        fallback = (
            getattr(self.options, auth.fallback_option, None) if auth.fallback_option else None
        )
        if isinstance(fallback, str) and fallback:
            return fallback
        if self._auth_token_path is not None and self._auth_token_path.is_file():
            existing = self._auth_token_path.read_text().strip()
            if existing:
                return existing
        return None

    def auth_enabled(self) -> bool:
        """True iff the auth scheme is ``jwt_enabled`` (no token needed)."""
        if self.config.auth is None:
            return False
        opt = self.config.auth.enabled_option
        return bool(opt and getattr(self.options, opt, False))

    def stop(self) -> StopResult:
        container = DockerContainer(self.config.container_name)
        result = container.stop()
        container.remove()
        return result

    def status(self) -> ServiceStatus:
        endpoint = (
            f"http://{HealthProbe.resolve_connect_host(self.config.listen_host)}"
            f":{self.config.listen_port}/"
        )
        if not DockerContainer(self.config.container_name).is_running():
            return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
        probe = HealthProbe(
            self.config.listen_host,
            self.config.listen_port,
            probe_path=self.config.health_probe_path,
        )
        if probe.probe():
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
    "AuthConfig",
    "DockerImageInstall",
    "DockerService",
    "DockerServiceConfig",
]
