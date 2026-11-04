"""Docker-backed ``DeclarativeServiceBase`` subclass.

Lifts the structural plumbing out of the per-service Docker
implementations (``services/crawl4ai``, ``services/sillytavern``).
Phase 3 will replace those with YAML specs that build
``DockerServiceConfig`` instances; phase 1 just ships the concrete
class and the unified installable.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...contracts import (
    InstallState,
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
from ..installables.docker_image import DockerImageInstall
from ..net import HealthProbe
from ..ports.markers import assert_no_markers
from ..process import DockerContainer
from .base import DeclarativeServiceBase, DeclarativeServiceConfig


@dataclass(frozen=True)
class ExtraPort:
    """One extra published port of a docker container.

    ``container_port`` is the port inside the container; ``host`` is the
    port on the node — a fixed integer or a ``$auto_port.<name>`` marker
    the framework allocates at start time (ADR-043).
    """

    container_port: int
    host: int | str


@dataclass(frozen=True)
class AuthConfig:
    """Optional API-token plumbing for docker services.

    ``enabled`` and ``fallback`` are resolved values from the YAML
    loader (not string references to option names). ``enabled`` is
    truthy when the service should advertise JWT mode; ``fallback``
    is the explicit user-typed override that wins over the on-disk
    file.
    """

    enabled: bool | None
    fallback: str | None
    token_env_var: str  # injected as env var in start()
    token_file: Path  # resolved against ctx.state_dir at construction
    token_file_mode: int
    token_generator: str


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
    listen_port: int | str = 0
    internal_port: int | None = None  # defaults to listen_port
    extra_ports: tuple[ExtraPort, ...] = ()
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
    # Security
    security_opts: list[str] | None = None
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
            secrets=ctx.secrets,
        )

    # --- introspection ----------------------------------------------------

    @property
    def has_image_page(self) -> bool:
        return True

    @property
    def image_ref(self) -> str:
        """Effective ref: recorded version selection wins over the default tag.

        Mirrors ``ComposeService._image_refs`` so picking a tag on the
        Images page changes what ``start()`` runs, not just what
        ``is_available()`` reports.
        """
        tag = self._install_obj.installed_version() or self.config.image_tag
        return f"{self.config.image_repo}:{tag}"

    @property
    def container_name(self) -> str:
        return self.config.container_name

    @property
    def listen_address(self) -> str | None:
        """The bind address, or ``None`` when the port is not known yet."""
        port = self.effective_host_port()
        if port is None:
            return None
        return f"{self.config.listen_host}:{port}"

    def own_container_names(self) -> tuple[str, ...]:
        return (self.config.container_name,)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def ui_panels(self) -> tuple[str, ...]:
        """Panel kinds the default status page renders for this service.

        Order: ``service_info`` first (always — carries the install /
        start / stop controls and Web UI link), then any YAML-declared
        ``status_panels`` (e.g. ``auth_token`` for crawl4ai), then the
        default ``container_info`` + ``log_tail``, then ``configure``
        last when the service declares ``options:``.

        ``configure`` lives at the bottom — opening the modal is a
        rare action compared to the live operational info above it,
        and an "Configure" button pinned above the running container
        state looks out of place.

        The ``configure`` panel is auto-included when the service
        declares any ``options:`` (ADR-036). YAML authors don't need
        to add ``- configure`` to ``ui.status_panels`` explicitly.
        """
        base_default = ("service_info", "container_info", "log_tail")
        yaml_extras = tuple(p for p in self.config.ui_pages if p not in base_default)
        # Auto-include ``configure`` at the end when the service has
        # user-editable options, so YAML authors don't have to remember
        # to add it. ``configure`` (when YAML-explicit) is filtered out
        # of the extras so it doesn't appear twice.
        non_configure_yaml = tuple(p for p in yaml_extras if p != "configure")
        auto_configure = ("configure",) if self.config.option_specs else ()
        return ("service_info", *non_configure_yaml, *base_default[1:], *auto_configure)

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
        port = self.effective_host_port()
        if port is None:
            return None
        path = self.config.web_ui_path or "/"
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/"):
            path = path + "/"
        return f"http://{self.public_host()}:{port}{path}"

    def tail_log(self, n_bytes: int = 8192) -> str:
        # Per the ADR, ``tail_lines`` is a sensible conversion of bytes-to-lines.
        tail_lines = max(50, n_bytes // 80)
        return DockerContainer(self.config.container_name).logs(tail_lines=tail_lines)

    def _ensure_volume_dirs(self) -> None:
        """Pre-create bind-mount targets as the host user.

        Without this, a container that initializes as root will create
        the bind-mount target as root, leaving the host user unable to
        remove it later. Pre-creating with the host uid/gid prevents
        that one failure mode; containers that ignore PUID/PGID may
        still write root-owned files inside the mount -- that's a
        container-side issue.
        """
        for host_path_str in self.config.extra_volumes.values():
            host_path = Path(host_path_str)
            try:
                host_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            if self._puid is not None and self._pgid is not None:
                try:
                    os.chown(host_path, self._puid, self._pgid)
                except OSError:
                    # chown fails on some filesystems or when not root
                    pass

    def start(self) -> StartResult:
        if not self.is_available():
            return StartResult(ok=False, message=f"image not pulled: {self.image_ref}")

        # Pre-create bind-mount targets as the host user before the
        # container initializes. See ``_ensure_volume_dirs`` for why.
        self._ensure_volume_dirs()

        # Pre-start hooks fire before env composition so their side effects
        # (e.g. generated token files) are visible to the env-builder below.
        # Hook entries are already-resolved dicts at this point -- the
        # loader substituted $state_dir / $data_dir at construction time,
        # so the runtime just dispatches by ``kind``.
        if self.config.pre_start_hooks:
            from .hooks import PreStartHookContext
            from .hooks import run as run_hooks

            run_hooks(
                list(self.config.pre_start_hooks),
                PreStartHookContext(
                    service=self,
                    state_dir=self._ctx.state_dir,
                    data_dir=self._ctx.data_dir,
                ),
            )

        container = DockerContainer(self.config.container_name)
        ports = self._port_map()

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
            if self.config.auth.enabled:
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
            security_opts=self.config.security_opts,
        )

    def _port_map(self) -> dict[str, tuple[str, int]]:
        """The ``-p`` map, with every auto-port marker materialised.

        The markers are resolved at start time — the point where the port
        is actually needed — through the same substitution engine the
        loader uses, with the allocator as the resolution source.
        """
        from .loader import resolve_placeholders

        resolved = resolve_placeholders(
            {
                "primary": self.config.listen_port,
                "extra": [
                    {"container": extra.container_port, "host": extra.host}
                    for extra in self.config.extra_ports
                ],
            },
            self._ctx,
            {},
            ports=self.port_binder().resolver(),
        )
        assert_no_markers(resolved, f"{self.name} port map")
        primary = int(resolved["primary"])
        ports = {f"{self.config.internal_port or primary}/tcp": (self.config.listen_host, primary)}
        for extra in resolved["extra"]:
            ports[f"{extra['container']}/tcp"] = (self.config.listen_host, int(extra["host"]))
        return ports

    def _resolve_or_generate_token(self) -> str:
        """Resolve the auth token to inject — file → generate.

        Explicit ``auth.fallback`` (the resolved user-typed option)
        wins outright. Otherwise the on-disk file is read; if absent,
        a 256-bit token is generated and persisted atomically with
        the configured mode.
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

        Reads ``fallback`` (the user-typed option, resolved at
        construction) first, then the on-disk file. Returns ``None``
        when ``enabled`` is truthy (token is irrelevant in that mode).
        """
        if self.config.auth is None:
            return None
        auth = self.config.auth
        if auth.enabled:
            return None
        if auth.fallback:
            return auth.fallback
        if self._auth_token_path is not None and self._auth_token_path.is_file():
            existing = self._auth_token_path.read_text().strip()
            if existing:
                return existing
        return None

    def auth_enabled(self) -> bool:
        """True iff the auth scheme is in JWT mode (no token needed)."""
        if self.config.auth is None:
            return False
        return bool(self.config.auth.enabled)

    def stop(self) -> StopResult:
        container = DockerContainer(self.config.container_name)
        result = container.stop()
        container.remove()
        return result

    def status(self) -> ServiceStatus:
        port = self.effective_host_port()
        endpoint = (
            None
            if port is None
            else f"http://{HealthProbe.resolve_connect_host(self.config.listen_host)}:{port}/"
        )
        if not DockerContainer(self.config.container_name).is_running():
            return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
        if port is None:
            return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)
        probe = HealthProbe(
            self.config.listen_host,
            port,
            probe_path=self.config.health_probe_path,
        )
        if probe.probe():
            return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
        return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)

    def wait_ready(self, timeout_s: float) -> bool:
        port = self.effective_host_port()
        if port is None:
            return False
        return HealthProbe(
            self.config.listen_host,
            port,
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
    "ExtraPort",
]
