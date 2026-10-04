"""ComfyUI inference service."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from ...contracts import (
    InferenceService,
    InstallState,
    ServiceCapabilities,
    ServiceCategory,
    ServiceContext,
    ServiceInstall,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)
from . import lifecycle
from .install import ComfyUiImage
from .options import ComfyUiOptions
from .symlinks import SymlinkApplier


class ComfyUiService(InferenceService):
    """Inference service for ComfyUI, running as a Docker container."""

    name = "comfyui"
    display_name = "ComfyUI"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        opts = ComfyUiOptions(**ctx.options)
        self._options = opts

        # GPU state comes from the framework-level host snapshot
        # (``ctx.host_info.hardware``); no per-service probe needed.
        self._hardware = ctx.host_info.hardware

        # Path defaults derived from ctx (ADR-025).
        # ctx.*_dir are already scoped to this service by the framework;
        # do not re-append the service name here (fixes double-"comfyui" bug).
        self._vault_models_dir = opts.vault_models_dir or ctx.vault_path / "comfyui"
        self._data_python_dir = opts.data_python_dir or ctx.data_dir / "data" / "python"
        self._data_custom_nodes_dir = (
            opts.data_custom_nodes_dir or ctx.data_dir / "data" / "custom_nodes"
        )
        # Inputs and outputs land under the media vault (ADR-037). The
        # ``media_vault_path`` is the framework-managed root for content
        # produced by services; other media-aware services can mount and
        # index the same root. Operators who want the legacy layout set
        # ``data_input_dir`` / ``data_output_dir`` explicitly.
        self._data_input_dir = opts.data_input_dir or ctx.media_vault_path / "comfyui" / "inputs"
        self._data_output_dir = opts.data_output_dir or ctx.media_vault_path / "comfyui" / "outputs"
        self._data_profiles_dir = opts.data_profiles_dir or ctx.data_dir / "data" / "profiles"
        self._symlinks_file = opts.symlinks_file or ctx.config_dir / "model_symlink.yaml"
        self._log_file = opts.log_file or ctx.log_dir / "comfyui.log"

        # PUID/PGID auto-default to the host user.
        self._puid = opts.puid if opts.puid is not None else os.getuid()
        self._pgid = opts.pgid if opts.pgid is not None else os.getgid()

        # Installables — one per GPU variant (ADR-040). Both share the
        # same data dirs / vault / container; the variant pick only
        # chooses which image the single container runs.
        common = {
            "data_dir": ctx.data_dir,
            "cache_dir": ctx.cache_dir,
            "state_dir": ctx.state_dir,
            "host_arch": opts.host_arch,
            "secrets": ctx.secrets,
        }
        self._install_cuda = ComfyUiImage(
            variant="cuda",
            image_repo=opts.image_repo,
            image_tag=opts.image_tag,
            **common,
        )
        self._install_rocm = ComfyUiImage(
            variant="rocm",
            image_repo=opts.rocm_image_repo,
            image_tag=opts.rocm_image_tag,
            **common,
        )
        self._installs = {"cuda": self._install_cuda, "rocm": self._install_rocm}

        # Symlink applier. The catalog is supplied by callers at apply /
        # list time; the service does not reach into the framework.
        self._symlinks = SymlinkApplier(
            symlinks_file=self._symlinks_file,
            vault_models_dir=self._vault_models_dir,
        )

    # --- introspection ----------------------------------------------------

    @property
    def has_nvidia_gpu(self) -> bool:
        return self._hardware.nvidia

    @property
    def has_amd_gpu(self) -> bool:
        return self._hardware.amd

    # --- GPU variant resolution (ADR-040) ---------------------------------
    # Mirrors llama_swap's llama_server_variant: an explicit user choice
    # (UI dropdown / options) wins, otherwise auto-pick from the host
    # snapshot. In-memory only; a worker restart reverts to auto-pick.

    @property
    def gpu_variant(self) -> str | None:
        return self._options.gpu_variant

    def set_gpu_variant(self, variant: str | None) -> None:
        """UI write path. ``None`` reverts to the auto-pick."""
        if variant not in ("cuda", "rocm", None):
            raise ValueError(f"unknown variant {variant!r}; expected cuda/rocm or None")
        self._options.gpu_variant = variant  # type: ignore[assignment]

    def set_extra_args(self, args: list[str]) -> None:
        """Update ``extra_args`` in memory (ADR-041).

        Persistence is the UI's job: the caller writes the value to the
        per-service JSON sidecar via ``worker.write_service_overrides``
        using the canonical ``paths.config_dir`` path. The service does
        not write the sidecar directly because ``ctx.config_dir`` is
        per-service scoped (``<base>/<service-name>``) while the
        framework reads the sidecar from ``<base>/services/<name>...``;
        routing through the worker keeps the two paths aligned.

        The in-memory update means ``start()`` picks up the new value
        without a worker restart; the sidecar write means a worker
        restart also picks it up via ``Settings.options_for``.
        """
        self._options.extra_args = list(args)

    @property
    def effective_gpu_variant(self) -> str:
        """The variant the container runs: pinned choice or snapshot pick.

        NVIDIA present -> cuda, else AMD present -> rocm, else cuda
        (preserves the pre-ROCm "no NVIDIA GPU" failure message on
        GPU-less hosts).
        """
        if self._options.gpu_variant is not None:
            return self._options.gpu_variant
        if self._hardware.nvidia:
            return "cuda"
        if self._hardware.amd:
            return "rocm"
        return "cuda"

    @property
    def active_install(self) -> ComfyUiImage:
        return self._installs[self.effective_gpu_variant]

    @property
    def image_ref(self) -> str:
        return self.active_install.image_ref

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
        # Availability is "the active variant's image pulled locally" —
        # consult state() directly.
        return self.active_install.state() == InstallState.INSTALLED

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

    @property
    def category(self) -> ServiceCategory:
        return ServiceCategory.IMAGE

    @property
    def description(self) -> str:
        return "Node-based image generation"

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
        variant = self.effective_gpu_variant
        has_variant_gpu = self._hardware.nvidia if variant == "cuda" else self._hardware.amd
        if self._options.gpu_required and not has_variant_gpu:
            vendor = "NVIDIA" if variant == "cuda" else "AMD"
            return StartResult(
                ok=False,
                message=f"no {vendor} GPU detected; set gpu_required=false to skip",
            )
        runtime: str | None = None
        gpu_flags: list[str] | None = None
        devices: list[str] | None = None
        group_add: str | None = None
        extra_env: dict[str, str] = {}
        if variant == "cuda" and has_variant_gpu:
            if self._hardware.nvidia_runtime:
                # Legacy path (Docker ≤28 with the nvidia OCI runtime
                # registered). The daemon honours `--runtime nvidia`; the
                # toolkit injects /dev/nvidia* via the OCI hook chain.
                runtime = self._options.runtime
                gpu_flags = [
                    f"driver={self._options.gpu_driver}",
                    f"count={self._options.gpu_count}",
                ]
            elif self._hardware.nvidia_cdi:
                # Modern path (Docker 29+ where the legacy OCI runtime is
                # no longer registered, but nvidia-container-toolkit's
                # CDI specs are present). Native `--gpus all` lets the
                # daemon inject devices via CDI — no `--runtime` needed.
                gpu_flags = ["all"]
            # else: NVIDIA card present but neither legacy runtime nor
            # CDI specs available. Container starts without a GPU; the
            # user sees the PyTorch "no NVIDIA driver" message and knows
            # to install nvidia-container-toolkit.
        elif variant == "rocm" and has_variant_gpu:
            # ROCm has no Docker runtime/CDI integration — the container
            # gets direct device passthrough (KFD + render nodes) and the
            # `video` group, plus the upstream compose env (ADR-040).
            devices = self._options.rocm_devices
            group_add = self._options.rocm_group_add
            extra_env = self._options.rocm_env

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
                # Mount the vault root at /vault so relative symlink targets
                # (e.g. ``../../huggingface/hub/...``) resolve correctly.
                "/vault": str(self._vault_models_dir.parent),
            },
            extra_args=["--models-directory", "/vault/comfyui", *self._options.extra_args],
            env={"PUID": str(self._puid), "PGID": str(self._pgid), **extra_env},
            runtime=runtime,
            gpu_flags=gpu_flags,
            devices=devices,
            group_add=group_add,
            restart_policy=self._options.restart_policy,
            hostname=self._options.container_name,
            vault_models_dir=self._vault_models_dir,
            media_input_dir=self._data_input_dir,
            media_output_dir=self._data_output_dir,
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
        return [self._install_cuda, self._install_rocm]

    def primary_installable(self) -> ServiceInstall | None:
        return self.active_install

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
            UiPage(
                "Image", ":material/inventory_2:", ui_dir / "image.py", url_path="comfyui_image"
            ),
            UiPage("Models", ":material/link:", ui_dir / "models.py", url_path="comfyui_models"),
            UiPage("Flags", ":material/flag:", ui_dir / "flags.py", url_path="comfyui_flags"),
        ]


__all__ = ["ComfyUiService"]
