"""Options this service accepts under ``settings.services.comfyui``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class ComfyUiOptions(BaseModel):
    # --- networking ---
    listen_host: str = "0.0.0.0"
    listen_port: int = 8188
    public_host: str | None = None

    # --- image (per-variant; ADR-040) ---
    image_repo: str = "ghcr.io/genesis-scaffolding/comfyui-cuda"
    image_tag: str = "v0.34.0-cuda-13.0-amd64"
    rocm_image_repo: str = "ghcr.io/genesis-scaffolding/comfyui-rocm"
    rocm_image_tag: str = "latest-rocm-7.2-amd64"
    # Which GPU variant's image the container runs. ``None`` = auto-pick
    # from the host snapshot (NVIDIA -> cuda, else AMD -> rocm, else
    # cuda). In-memory only (mirrors llama_swap's llama_server_variant);
    # a worker restart reverts to auto-pick.
    gpu_variant: Literal["cuda", "rocm"] | None = None
    # Host architecture suffix used to filter image tags. ``None`` means
    # "auto-detect from ``platform.machine()``". Set explicitly to skip
    # auto-detection, e.g. ``host_arch=""`` to disable filtering.
    host_arch: str | None = None

    # --- container identity ---
    container_name: str = "comfyui"
    health_timeout_s: float = 90.0
    log_file: Path | None = None

    # --- runtime / GPU ---
    gpu_required: bool = True
    runtime: str = "nvidia"
    gpu_driver: str = "nvidia"
    gpu_count: str = "1"
    # ROCm container plumbing (ADR-040). ``/dev/kfd`` is the KFD compute
    # entry point, ``/dev/dri`` the render nodes; ``video`` group is
    # required to open ``renderD*``. The env values follow the upstream
    # compose: empty ``CUDA_VISIBLE_DEVICES`` disambiguates from a host
    # CUDA setup, and AOTriton is load-bearing for GPU attention kernels
    # (without it torch falls back to the CPU math backend).
    rocm_devices: list[str] = ["/dev/kfd", "/dev/dri"]
    rocm_group_add: str = "video"
    rocm_env: dict[str, str] = {
        "HIP_VISIBLE_DEVICES": "0",
        "CUDA_VISIBLE_DEVICES": "",
        "TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL": "1",
    }
    restart_policy: str = "unless-stopped"
    puid: int | None = None
    pgid: int | None = None

    # --- bind mounts (host paths; defaults are derived from ctx at construction) ---
    data_python_dir: Path | None = None
    data_custom_nodes_dir: Path | None = None
    data_input_dir: Path | None = None
    data_output_dir: Path | None = None
    data_profiles_dir: Path | None = None
    vault_models_dir: Path | None = None

    # --- symlinks ---
    symlinks_file: Path | None = None

    # --- extra container args ---
    extra_args: list[str] = ["--verbose"]


__all__ = ["ComfyUiOptions"]
