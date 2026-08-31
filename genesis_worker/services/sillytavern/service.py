"""SillyTavern inference service — a Docker-container chat UI."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from ...contracts import (
    InferenceService,
    InstallState,
    ServiceCapabilities,
    ServiceContext,
    ServiceInstall,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)
from . import lifecycle
from .config import seed_config
from .install import SillyTavernImage
from .options import SillyTavernOptions


class SillyTavernService(InferenceService):
    """Service for the SillyTavern chat UI, running as a Docker container.

    SillyTavern is an LLM front-end, not an inference backend: it reports
    ``has_web_ui`` / ``can_install`` but ``can_serve_llm`` is False (the
    llama-swap service remains the LLM). There is no host-GPU dependency.
    """

    name = "sillytavern"
    display_name = "SillyTavern"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        opts = SillyTavernOptions(**ctx.options)
        self._options = opts

        # Path defaults derived from ctx (ADR-025). ctx.*_dir are already
        # scoped to this service; do not re-append the service name here.
        self._config_path = opts.config_path or ctx.data_dir / "config"
        self._data_path = opts.data_path or ctx.data_dir / "data"
        self._extensions_path = opts.extensions_path
        self._plugins_path = opts.plugins_path
        self._log_file = opts.log_file or ctx.log_dir / "sillytavern.log"

        # PUID/PGID auto-default to the host user, like comfyui.
        self._puid = opts.puid if opts.puid is not None else os.getuid()
        self._pgid = opts.pgid if opts.pgid is not None else os.getgid()

        # Installable.
        self._install = SillyTavernImage(
            data_dir=ctx.data_dir,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            image_repo=opts.image_repo,
            image_tag=opts.image_tag,
            secrets=ctx.secrets,
        )

    # --- introspection ----------------------------------------------------

    @property
    def image_ref(self) -> str:
        return f"{self._options.image_repo}:{self._options.image_tag}"

    @property
    def listen_address(self) -> str:
        return f"{self._options.listen_host}:{self._options.listen_port}"

    @property
    def log_file(self) -> Path:
        return self._log_file

    # --- contract overrides -----------------------------------------------

    def is_available(self) -> bool:
        # There is no host binary for a container service.
        return self._install.state() == InstallState.INSTALLED

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        )

    def resource_estimate(self) -> ServiceResourceEstimate:
        # No GPU; SillyTavern is a Node.js chat UI, light on resources.
        return ServiceResourceEstimate(
            vram_bytes_typical=0,
            vram_bytes_min=0,
            cpu_cores_recommended=2,
        )

    def is_running(self) -> bool:
        return lifecycle.is_running_sillytavern(self._options.container_name)

    def runtime_endpoint(self) -> str | None:
        return None

    def web_ui_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        return f"http://{self.public_host()}:{self._options.listen_port}/"

    def tail_log(self, n_bytes: int = 8192) -> str:
        tail_lines = max(50, n_bytes // 80)
        return lifecycle.logs_sillytavern(self._options.container_name, tail_lines)

    def public_host(self) -> str:
        if self._options.public_host:
            return self._options.public_host
        try:
            return socket.gethostname()
        except OSError:
            return "localhost"

    def start(self) -> StartResult:
        volumes = {
            "/home/node/app/config": str(self._config_path),
            "/home/node/app/data": str(self._data_path),
        }
        if self._extensions_path is not None:
            volumes["/home/node/app/public/scripts/extensions/third-party"] = str(
                self._extensions_path
            )
        if self._plugins_path is not None:
            volumes["/home/node/app/plugins"] = str(self._plugins_path)

        env = {"PUID": str(self._puid), "PGID": str(self._pgid)}

        # SillyTavern's default Docker whitelist blocks the host on Linux
        # (whitelistDockerHosts can't resolve the gateway). Seed config.yaml
        # before the container starts so published host traffic is allowed.
        seed_config(self._config_path)

        return lifecycle.start_sillytavern(
            image=self.image_ref,
            image_present=self.is_available(),
            container_name=self._options.container_name,
            listen_host=self._options.listen_host,
            listen_port=self._options.listen_port,
            volumes=volumes,
            env=env,
            restart_policy=self._options.restart_policy,
            hostname=self._options.container_name,
            extra_args=self._options.extra_args,
        )

    def stop(self) -> StopResult:
        return lifecycle.stop_sillytavern(self._options.container_name)

    def status(self) -> ServiceStatus:
        return lifecycle.status_sillytavern(
            self._options.container_name,
            self._options.listen_host,
            self._options.listen_port,
        )

    def wait_ready(self, timeout_s: float) -> bool:
        return lifecycle.wait_ready_sillytavern(
            self._options.listen_host,
            self._options.listen_port,
            timeout_s,
        )

    # --- install axis -----------------------------------------------------

    def installs(self) -> list[ServiceInstall]:
        return [self._install]

    def primary_installable(self) -> ServiceInstall | None:
        return self._install

    def uninstall_installable(self, name: str, *, version: str | None = None) -> None:
        """Remove an installable's installed version. Refuses if the service is running."""
        if self.is_running():
            raise RuntimeError(
                f"cannot uninstall {name!r} while {self.display_name} is running — "
                "stop the service first"
            )
        for installable in self.installs():
            if installable.name == name:
                installable.uninstall(version=version)
                return
        raise KeyError(f"unknown installable {name!r}")

    # --- UI ---------------------------------------------------------------

    @property
    def ui_pages(self) -> list[UiPage]:
        ui_dir = Path(__file__).parent / "ui"
        return [
            UiPage(
                "Status", ":material/monitor:", ui_dir / "status.py", url_path="sillytavern_status"
            ),
            UiPage(
                "Image", ":material/inventory_2:", ui_dir / "image.py", url_path="sillytavern_image"
            ),
        ]


__all__ = ["SillyTavernService"]
