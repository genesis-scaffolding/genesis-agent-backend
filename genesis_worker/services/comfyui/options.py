"""Options this service accepts under ``settings.services.comfyui``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class ComfyUiOptions(BaseModel):
    # --- networking ---
    listen_host: str = "0.0.0.0"
    listen_port: int = 8188
    public_host: str | None = None

    # --- image ---
    image_repo: str = "ghcr.io/genesis-scaffolding/comfyui-cuda"
    image_tag: str = "v0.34.0-cuda-13.0-amd64"
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
