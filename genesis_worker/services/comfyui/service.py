"""ComfyUI inference service."""

from __future__ import annotations

import os
import socket
import subprocess
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
from ...utils.process import DockerContainer
from . import lifecycle
from .install import ComfyUiImage
from .options import ComfyUiOptions
from .symlinks import SymlinkApplier


def _has_nvidia_gpu() -> bool:
    """Probe ``nvidia-smi -L``. Robust against missing binary and hangs.

    Cached at service construction; re-probing is unnecessary on a
    typical session. The future framework-level host-info work
    replaces this with a single probe shared across services (ADR-025).
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            timeout=5,
            text=True,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and "GPU" in result.stdout


class ComfyUiService(InferenceService):
    """Inference service for ComfyUI, running as a Docker container."""

    name = "comfyui"
    display_name = "ComfyUI"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        opts = ComfyUiOptions(**ctx.options)
        self._options = opts

        # Cached GPU probe.
        self._has_nvidia_gpu = _has_nvidia_gpu()

        # Path defaults derived from ctx (ADR-025).
        # ctx.*_dir are already scoped to this service by the framework;
        # do not re-append the service name here (fixes double-"comfyui" bug).
        self._vault_models_dir = opts.vault_models_dir or ctx.vault_path / "comfyui"
        self._data_python_dir = opts.data_python_dir or ctx.data_dir / "data" / "python"
        self._data_custom_nodes_dir = (
            opts.data_custom_nodes_dir or ctx.data_dir / "data" / "custom_nodes"
        )
        self._data_input_dir = opts.data_input_dir or ctx.data_dir / "data" / "input"
        self._data_output_dir = opts.data_output_dir or ctx.data_dir / "data" / "output"
        self._data_profiles_dir = opts.data_profiles_dir or ctx.data_dir / "data" / "profiles"
        self._symlinks_file = opts.symlinks_file or ctx.config_dir / "model_symlink.yaml"
        self._log_file = opts.log_file or ctx.log_dir / "comfyui.log"

        # PUID/PGID auto-default to the host user.
        self._puid = opts.puid if opts.puid is not None else os.getuid()
        self._pgid = opts.pgid if opts.pgid is not None else os.getgid()

        # Installable.
        self._install = ComfyUiImage(
            data_dir=ctx.data_dir,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            image_repo=opts.image_repo,
            image_tag=opts.image_tag,
            host_arch=opts.host_arch,
            secrets=ctx.secrets,
        )

        # Symlink applier. The catalog is supplied by callers at apply /
        # list time; the service does not reach into the framework.
        self._symlinks = SymlinkApplier(
            symlinks_file=self._symlinks_file,
            vault_models_dir=self._vault_models_dir,
        )

    # --- introspection ----------------------------------------------------

    @property
    def has_nvidia_gpu(self) -> bool:
        return self._has_nvidia_gpu

    @property
    def image_ref(self) -> str:
        return f"{self._options.image_repo}:{self._options.image_tag}"

    @property
    def listen_address(self) -> str:
        return f"{self._options.listen_host}:{self._options.listen_port}"

    @property
    def log_file(self) -> Path:
        return self._log_file

    @property
    def symlinks(self) -> SymlinkApplier:
        return self._symlinks

    # --- contract overrides -----------------------------------------------

    def is_available(self) -> bool:
        # Override: there is no host binary for a container service.
        # Availability is "image pulled locally" — consult state() directly.
        return self._install.state() == InstallState.INSTALLED

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=True,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        )

    def resource_estimate(self) -> ServiceResourceEstimate:
        return ServiceResourceEstimate(
            vram_bytes_typical=12_000_000_000,
            vram_bytes_min=6_000_000_000,
            cpu_cores_recommended=4,
        )

    def is_running(self) -> bool:
        return lifecycle.is_running_comfyui(self._options.container_name)

    def runtime_endpoint(self) -> str | None:
        # No OpenAI-compatible API on ComfyUI.
        return None

    def web_ui_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        return f"http://{self.public_host()}:{self._options.listen_port}/"

    def tail_log(self, n_bytes: int = 8192) -> str:
        # docker logs is line-oriented; approximate the byte count.
        tail_lines = max(50, n_bytes // 80)
        return lifecycle.logs_comfyui(self._options.container_name, tail_lines)

    def public_host(self) -> str:
        if self._options.public_host:
            return self._options.public_host
        try:
            return socket.gethostname()
        except OSError:
            return "localhost"

    def start(self) -> StartResult:
        if self._options.gpu_required and not self._has_nvidia_gpu:
            return StartResult(
                ok=False,
                message="no NVIDIA GPU detected; set gpu_required=false to skip",
            )
        runtime: str | None = None
        gpu_flags: list[str] | None = None
        if self._options.gpu_required and DockerContainer.nvidia_runtime_available():
            runtime = self._options.runtime
            gpu_flags = [
                f"driver={self._options.gpu_driver}",
                f"count={self._options.gpu_count}",
            ]

        return lifecycle.start_comfyui(
            image=self.image_ref,
            image_present=self.is_available(),
            container_name=self._options.container_name,
            listen_host=self._options.listen_host,
            listen_port=self._options.listen_port,
            volumes={
                "/opt/comfyui/python": str(self._data_python_dir),
                "/opt/comfyui/app/custom_nodes": str(self._data_custom_nodes_dir),
                "/opt/comfyui/app/input": str(self._data_input_dir),
                "/opt/comfyui/app/output": str(self._data_output_dir),
                "/opt/comfyui/app/user": str(self._data_profiles_dir),
                "/opt/comfyui/app/models": str(self._vault_models_dir),
            },
            env={"PUID": str(self._puid), "PGID": str(self._pgid)},
            runtime=runtime,
            gpu_flags=gpu_flags,
            extra_args=self._options.extra_args,
            restart_policy=self._options.restart_policy,
            hostname=self._options.container_name,
        )

    def stop(self) -> StopResult:
        return lifecycle.stop_comfyui(self._options.container_name)

    def status(self) -> ServiceStatus:
        return lifecycle.status_comfyui(
            self._options.container_name,
            self._options.listen_host,
            self._options.listen_port,
        )

    def wait_ready(self, timeout_s: float) -> bool:
        return lifecycle.wait_ready_comfyui(
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
            UiPage("Status", ":material/monitor:", ui_dir / "status.py", url_path="comfyui_status"),
            UiPage("Image", ":material/inventory_2:", ui_dir / "image.py", url_path="comfyui_image"),
            UiPage("Models", ":material/link:", ui_dir / "models.py", url_path="comfyui_models"),
        ]


__all__ = ["ComfyUiService"]
