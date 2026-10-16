"""Compose-backed ``DeclarativeServiceBase`` subclass.

Multi-container services use a ``docker-compose.yml`` (a plain string,
populated by the standard $placeholder mechanism at load time) instead of
a single ``docker run`` invocation. Lifecycle delegates to
``DockerComposeStack``; install axis uses ``ComposeImageInstall`` which
checks and pulls all images referenced in the rendered compose file.
"""

from __future__ import annotations

import os
import re
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
from ..net import HealthProbe
from ..process import DockerContainer
from .base import DeclarativeServiceBase, DeclarativeServiceConfig
from .compose_stack import DockerComposeStack
from .docker_service import DockerImageInstall

_IMAGE_RE = re.compile(r"^\s*image:\s*['\"]?([^'\"\s]+)['\"]?", re.MULTILINE)


@dataclass(frozen=True)
class ComposeImage:
    """One image in a compose stack.

    ``repo`` is the image repository (no tag). ``tag_option`` names the
    service option that carries the tag; the recorded selection (see
    ``DockerImageInstall``) wins at render time once the user has
    installed a version.
    """

    name: str
    repo: str
    tag_option: str = ""


def _substitute_tag_markers(text: str, subs: dict[str, str]) -> str:
    """Replace ``$options.<name>`` markers present in ``subs`` with their values.

    Only the markers the caller listed are touched — any other
    ``$options.X`` in the text is left intact (there shouldn't be any;
    the loader resolves the rest at load time).
    """
    for name, value in subs.items():
        text = text.replace(f"$options.{name}", value)
    return text


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
    # ``$options.X`` markers left in the template for declared image tags;
    # substituted at render time with the recorded selection (or the
    # load-time default when nothing has been installed yet).
    render_substitutions: dict[str, str] = field(default_factory=dict)


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
                image_tag=config.render_substitutions.get(img.tag_option, "latest"),
                cache_dir=ctx.cache_dir,
                state_dir=ctx.state_dir / "install" / img.name,
                name=f"{config.name}:{img.name}",
                source_url=None,
            )
            for img in config.images
        ]
        self._puid = os.getuid()
        self._pgid = os.getgid()

    # --- introspection ----------------------------------------------------

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

    def _stack_images(self) -> list[str]:
        """Every image in the template, at its effective tag."""
        subs = self._tag_substitutions()
        return [
            _substitute_tag_markers(img, subs) for img in _IMAGE_RE.findall(self.config.compose_template)
        ]

    def _tag_substitutions(self) -> dict[str, str]:
        """Map ``$options.<tag_option>`` markers to their effective tag.

        The recorded selection (a version the user installed) wins over
        the load-time default, so a GUI version pick reaches the
        compose file on the next start.
        """
        subs: dict[str, str] = dict(self.config.render_substitutions)
        for img, installable in zip(self.config.images, self._installables):
            if img.tag_option:
                selected = installable.installed_version()
                subs[img.tag_option] = selected or subs.get(img.tag_option, "latest")
        return subs

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
        the declared image tag markers (``$options.<tag_option>``), which
        are substituted here with the effective tag — recorded selection
        wins over the load-time default.
        """
        subs = self._tag_substitutions()
        body = _substitute_tag_markers(self.config.compose_template, subs)
        self._compose_dir.mkdir(parents=True, exist_ok=True)
        path = self._compose_dir / "docker-compose.yml"
        path.write_text(body)
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
        return self._installables


__all__ = [
    "ComposeImage",
    "ComposeService",
    "ComposeServiceConfig",
]
