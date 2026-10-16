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
    AcquireChoice,
    AcquireProgress,
    AcquireState,
    AcquireStateKind,
    AcquireView,
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
from ..background_session import BackgroundSession, _Canceled
from ..net import HealthProbe
from ..process import DockerContainer
from .base import DeclarativeServiceBase, DeclarativeServiceConfig
from .compose_stack import DockerComposeStack

_IMAGE_RE = re.compile(r"^\s*image:\s*['\"]?([^'\"\s]+)['\"]?", re.MULTILINE)


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


# --- image install ----------------------------------------------------------


class ComposeImageInstall(ServiceInstall):
    """Installable that checks and pulls every image referenced in the compose file.

    ``is_available()`` is True when all images are present locally.
    ``install()`` pulls each image in sequence via ``ComposePullSession``.
    """

    source_name = "compose_images"

    def __init__(
        self,
        *,
        compose_template: str,
        cache_dir: Path,
        state_dir: Path,
        name: str,
    ) -> None:
        self._compose_template = compose_template
        self._cache_dir = cache_dir
        self._state_dir = state_dir
        self._selection_path = state_dir / "current"
        self.name = name

    def _images(self) -> list[str]:
        """Parse ``image:`` lines from the resolved compose template."""
        return _IMAGE_RE.findall(self._compose_template)

    def state(self) -> InstallState:
        images = self._images()
        if not images:
            return InstallState.INSTALLED
        if all(DockerContainer.image_present(img) for img in images):
            return InstallState.INSTALLED
        return InstallState.NOT_INSTALLED

    def installed_version(self) -> str | None:
        import hashlib

        if not self._selection_path.is_file():
            return None
        h = hashlib.sha256(self._compose_template.encode()).hexdigest()[:12]
        return f"template-{h}"

    def available_versions(self) -> list[InstallVersion]:
        return [
            InstallVersion(
                version=img,
                url=img,
                sha256=None,
                size_bytes=None,
            )
            for img in self._images()
        ]

    def invalidate_versions_cache(self) -> None:
        pass

    def binary_path(self) -> Path | None:
        return None

    def install(self, *, version: str | None = None) -> Any:
        return ComposePullSession(images=self._images(), state_dir=self._state_dir)

    def uninstall(self, *, version: str | None = None) -> None:
        pass


class ComposePullSession(BackgroundSession):
    """Sequential multi-image pull session.

    Pulls images one at a time. The UI sees one ``FETCHING`` step
    per image; the session transitions to ``COMPLETE`` once all succeed.
    """

    source_name = "compose_images"

    def __init__(self, images: list[str], state_dir: Path) -> None:
        first = images[0] if images else "unknown"
        state = AcquireState(
            kind=AcquireStateKind.FETCHING,
            repo_id=first,
        )
        super().__init__(state)
        self._images = images
        self._state_dir = state_dir
        self._index = 0
        self._total = len(images)
        self._current = first
        self._start()

    @property
    def repo_id(self) -> str:
        return self._current

    def view(self) -> AcquireView:
        kind = self._state.kind
        if kind == AcquireStateKind.FETCHING:
            return AcquireView(
                kind=kind,
                title=f"Pulling {self._current} ({self._index + 1}/{self._total})",
                progress=AcquireProgress(
                    bytes_done=0,
                    bytes_total=0,
                    speed_bps=0,
                    eta_s=0,
                ),
                log_tail=self._state.log_tail[-20:],
                can_cancel=True,
            )
        if kind == AcquireStateKind.COMPLETE:
            return AcquireView(
                kind=kind,
                title=f"Pulled {self._total} image(s)",
                can_cancel=False,
            )
        if kind == AcquireStateKind.FAILED:
            return AcquireView(
                kind=kind,
                title=f"Failed: {self._current}",
                error=self._state.failure,
                log_tail=self._state.log_tail[-20:],
                can_cancel=False,
            )
        if kind == AcquireStateKind.CANCELLED:
            return AcquireView(kind=kind, title="Cancelled", can_cancel=False)
        return AcquireView(kind=kind, title=f"Pulling {self._current}", can_cancel=True)

    def submit(self, choice: AcquireChoice) -> None:
        pass

    def _run_inner(self) -> None:
        for i, image in enumerate(self._images):
            self._index = i
            self._current = image
            self._state.repo_id = image
            try:
                DockerContainer.pull(
                    image,
                    progress=self._on_progress,
                    cancel=self._cancel_event.is_set,
                    timeout_s=1800.0,
                )
            except _Canceled:
                raise
            except RuntimeError as exc:
                self._state.failure = str(exc)
                self._state.kind = AcquireStateKind.FAILED
                return
        self._state.kind = AcquireStateKind.COMPLETE

    def _on_progress(self, line: str) -> None:
        if self._cancel_event.is_set():
            raise _Canceled
        self._state.log_tail.append(line)
        if len(self._state.log_tail) > 200:
            self._state.log_tail = self._state.log_tail[-200:]


# --- service ---------------------------------------------------------------


class ComposeService(DeclarativeServiceBase[ComposeServiceConfig]):
    """Declarative service that runs as a docker compose stack.

    Lifecycle delegates to ``DockerComposeStack``. ``is_available()``
    is True when every image referenced in the compose template is
    present locally. ``start()`` writes the resolved template to
    ``docker-compose.yml``, then runs ``docker compose up -d``.
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
        self._install_obj = ComposeImageInstall(
            compose_template=config.compose_template,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            name=config.name,
        )
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

    def is_available(self) -> bool:
        return self._install_obj.state() == InstallState.INSTALLED

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
        """Write the (already $placeholder-resolved) compose template to disk.

        The loader substituted every ``$placeholder`` at load time, so the
        stored template is final — no second templating pass here.
        """
        self._compose_dir.mkdir(parents=True, exist_ok=True)
        path = self._compose_dir / "docker-compose.yml"
        path.write_text(self.config.compose_template)
        return path

    def start(self) -> StartResult:
        if not self.is_available():
            images = self._install_obj._images()
            return StartResult(
                ok=False,
                message=f"not all images are available: {images}; install the service first",
            )

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

        self._render_compose_file()

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
        return [self._install_obj]


__all__ = [
    "ComposeImageInstall",
    "ComposePullSession",
    "ComposeService",
    "ComposeServiceConfig",
]
