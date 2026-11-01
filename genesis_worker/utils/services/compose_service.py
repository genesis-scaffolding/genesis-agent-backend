"""Compose-backed ``DeclarativeServiceBase`` subclass.

Multi-container services use a ``docker-compose.yml`` (a plain string,
populated by the standard $placeholder mechanism at load time) instead of
a single ``docker run`` invocation. Lifecycle delegates to
``DockerComposeStack``; the install axis is one ``DockerImageInstall`` per
declared image (real registry versioning, GUI version pick).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ...contracts import (
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
from ..net import HealthProbe
from ..process import DockerContainer
from .base import DeclarativeServiceBase, DeclarativeServiceConfig
from .compose_stack import DockerComposeStack
from .docker_service import DockerImageInstall

_IMAGE_RE = re.compile(r"^\s*image:\s*['\"]?([^'\"\s]+)['\"]?", re.MULTILINE)


@dataclass(frozen=True)
class ComposeImage:
    """One image in a compose stack (framework-level, not a user option).

    ``repo`` is the pull reference (registry path — ``rommapp/romm`` for
    Docker Hub, ``ghcr.io/org/img`` for GHCR). ``default_tag`` is the tag
    used when the user has not picked a version; a recorded selection
    (see ``DockerImageInstall``) wins at render time. Image identity is
    owned by the framework, so it is NOT exposed in the options panel —
    that surface is reserved for app-tunable settings.
    """

    name: str
    repo: str
    default_tag: str = "latest"


@dataclass(frozen=True)
class ComposeServiceConfig(DeclarativeServiceConfig):
    """Per-service compose config. Add identity / capabilities / options_model in caller."""

    category: ServiceCategory = ServiceCategory.OTHER
    compose_template: str = ""
    compose_version: str = "3.8"
    listen_host: str = "0.0.0.0"
    listen_port: int = 0
    health_probe_path: str = "/"
    extra_env: dict[str, Any] = field(default_factory=dict)
    extra_volumes: dict[str, Any] = field(default_factory=dict)
    pre_start_hooks: tuple[dict, ...] = ()
    data_dir_subpath: str | None = "data"
    # Every image the compose stack uses. Each gets its own
    # DockerImageInstall (real registry versioning, GUI version pick).
    # Rule: if it's in the template, it's declared here.
    images: tuple[ComposeImage, ...] = ()


# --- service ---------------------------------------------------------------


class ComposeService(DeclarativeServiceBase[ComposeServiceConfig]):
    """Declarative service that runs as a docker compose stack.

    Lifecycle delegates to ``DockerComposeStack``. ``is_available()``
    is True when every image referenced in the compose template is
    present locally. ``start()`` writes the resolved template to
    ``docker-compose.yml``, then runs ``docker compose up -d``.

    Install axis: one ``DockerImageInstall`` per declared image (each with
    real registry versioning). Every image in the template is declared in
    the YAML, so a full install leaves nothing missing.
    """

    def __init__(self, ctx: ServiceContext, *, config: ComposeServiceConfig) -> None:
        super().__init__(ctx, config=config)
        if config.data_dir_subpath:
            self._data_dir = ctx.data_dir / config.data_dir_subpath
        else:
            self._data_dir = ctx.data_dir
        self._compose_dir = ctx.state_dir / "compose"
        self._project_name = f"{config.name}-stack"
        self._stack = DockerComposeStack(self._compose_dir, self._project_name)
        self._installables = [
            DockerImageInstall(
                image_repo=img.repo,
                image_tag=img.default_tag,
                cache_dir=ctx.cache_dir,
                state_dir=ctx.state_dir / "install" / img.name,
                name=f"{config.name}:{img.name}",
                source_url=None,
                secrets=ctx.secrets,
            )
            for img in config.images
        ]
        self._puid = os.getuid()
        self._pgid = os.getgid()

    # --- introspection ----------------------------------------------------

    @property
    def has_image_page(self) -> bool:
        return True

    @property
    def compose_dir(self) -> Path:
        return self._compose_dir

    @property
    def listen_address(self) -> str:
        return f"{self.config.listen_host}:{self.config.listen_port}"

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def ui_panels(self) -> tuple[str, ...]:
        base_default = ("service_info", "container_info", "log_tail")
        yaml_extras = tuple(p for p in self.config.ui_pages if p not in base_default)
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

    def _image_refs(self) -> dict[str, str]:
        """Effective ``$images.<name>`` refs: recorded selection wins over ``default_tag``."""
        return {
            img.name: f"{img.repo}:{installable.installed_version() or img.default_tag}"
            for img, installable in zip(self.config.images, self._installables, strict=True)
        }

    def _rendered_template(self) -> str:
        """The compose template with ``$images.<name>`` at its effective ref.

        Same placeholder engine as the load-time pass, supplied with the
        image refs — the only thing that can't be known at load time is
        the user's recorded version selection (the installables are
        constructed in ``__init__``).
        """
        from .loader import resolve_placeholders

        ctx = self._ctx
        return resolve_placeholders(
            self.config.compose_template, ctx, {}, None, images=self._image_refs()
        )

    def _stack_images(self) -> list[str]:
        """Every image in the template, at its effective ref."""
        return _IMAGE_RE.findall(self._rendered_template())

    def is_available(self) -> bool:
        return all(DockerContainer.image_present(img) for img in self._stack_images())

    def is_running(self) -> bool:
        return self._stack.is_running()

    def runtime_endpoint(self) -> str | None:
        return None

    def web_ui_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        path = self.config.health_probe_path or "/"
        if not path.startswith("/"):
            path = "/" + path
        return f"http://{self.public_host()}:{self.config.listen_port}{path}"

    def tail_log(self, n_bytes: int = 8192) -> str:
        tail_lines = max(50, n_bytes // 80)
        return self._stack.logs(tail_lines=tail_lines)

    def _ensure_volume_dirs(self) -> None:
        for host_path_str in self.config.extra_volumes.values():
            host_path = Path(str(host_path_str))
            try:
                host_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            try:
                os.chown(host_path, self._puid, self._pgid)
            except OSError:
                pass

    def _render_compose_file(self) -> Path:
        """Write the compose file to disk.

        The loader substituted every ``$placeholder`` at load time, except
        the image markers (``$images.<name>``), which are resolved here
        with the effective ref — recorded selection wins over default_tag.

        Mode 0600: the rendered file can carry credentials (the ingress
        proxy's DNS key, a database password), so it is not world
        readable.
        """
        body = self._rendered_template()
        self._compose_dir.mkdir(parents=True, exist_ok=True)
        path = self._compose_dir / "docker-compose.yml"
        path.write_text(body)
        path.chmod(0o600)
        return path

    def start(self) -> StartResult:
        self._ensure_volume_dirs()

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

        # Render with the effective tags, then pull anything missing. A full
        # ``docker compose pull`` makes start() self-sufficient: it fetches
        # every declared image at its rendered tag (idempotent — present
        # images are skipped), so a fresh install never hits a half-pulled
        # stack. The UI install axis still records version selections;
        # this just guarantees the whole stack exists before ``up``.
        self._render_compose_file()

        pull = self._stack.pull()
        if not pull.ok:
            return pull

        env: dict[str, str] = {k: str(v) for k, v in self.config.extra_env.items()}
        return self._stack.up(env=env)

    def stop(self) -> StopResult:
        return self._stack.down()

    def status(self) -> ServiceStatus:
        endpoint = (
            f"http://{HealthProbe.resolve_connect_host(self.config.listen_host)}"
            f":{self.config.listen_port}/"
        )
        if not self._stack.is_running():
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
        return cast("list[ServiceInstall]", self._installables)


__all__ = [
    "ComposeImage",
    "ComposeService",
    "ComposeServiceConfig",
]
