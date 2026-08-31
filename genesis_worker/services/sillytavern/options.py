"""Options this service accepts under ``settings.services.sillytavern``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class SillyTavernOptions(BaseModel):
    # --- networking ---
    # ``listen_port`` is the *host/published* port; the container listens on
    # 8000 internally and is mapped to this port. ``0.0.0.0`` exposes it on
    # the LAN/VPN.
    listen_host: str = "0.0.0.0"
    listen_port: int = 9090
    public_host: str | None = None

    # --- image ---
    image_repo: str = "ghcr.io/sillytavern/sillytavern"
    # ``latest`` tracks the stable release; ``staging`` tracks the nightly.
    # The registry listing (``SillyTavernImage.available_versions``) lets the
    # user pick any other tag too.
    image_tag: str = "latest"

    # --- container identity ---
    container_name: str = "sillytavern"
    health_timeout_s: float = 60.0
    log_file: Path | None = None

    # --- runtime ---
    restart_policy: str = "unless-stopped"
    # PUID/PGID default to the host user so files created in mounted volumes
    # are owned by the host user (cf. comfyui). ``None`` keeps the image
    # default (root).
    puid: int | None = None
    pgid: int | None = None

    # --- bind mounts (host paths; defaults are derived from ctx at
    # construction). config + data are mandatory per the upstream Docker
    # docs; extensions + plugins are optional. ---
    config_path: Path | None = None
    data_path: Path | None = None
    extensions_path: Path | None = None
    plugins_path: Path | None = None

    # --- extra container args ---
    extra_args: list[str] = []


__all__ = ["SillyTavernOptions"]
