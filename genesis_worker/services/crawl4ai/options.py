"""Options this service accepts under ``settings.services.crawl4ai``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Crawl4AiOptions(BaseModel):
    # --- networking ---
    # ``listen_port`` is the *host/published* port; the container listens on
    # 11235 internally and is mapped to this port. ``0.0.0.0`` exposes it on
    # the LAN/VPN.
    listen_host: str = "0.0.0.0"
    listen_port: int = 11235
    public_host: str | None = None

    # --- image ---
    image_repo: str = "unclecode/crawl4ai"
    image_tag: str = "latest"

    # --- container identity ---
    container_name: str = "crawl4ai"
    health_timeout_s: float = 60.0
    log_file: Path | None = None

    # --- runtime ---
    restart_policy: str = "unless-stopped"
    # PUID/PGID default to the host user so files created in mounted volumes
    # are owned by the host user (cf. comfyui/sillytavern). ``None`` keeps the
    # image default (root).
    puid: int | None = None
    pgid: int | None = None
    # crawl4ai runs Playwright/Chromium internally; the 64 MB docker default
    # is too small and the browser will crash. The upstream docs recommend 1g.
    shm_size: str = "1g"

    # --- auth ---
    # crawl4ai's upstream entrypoint refuses to bind gunicorn to non-loopback
    # unless one of these is set (otherwise the API is reachable only from
    # inside the container, which docker port mapping can't traverse).
    # ``jwt_enabled`` wins outright — different auth mechanism. An explicit
    # ``api_token`` overrides the auto-generated persistent token so the
    # user's setting is never silently overwritten.
    api_token: str | None = None
    jwt_enabled: bool = False

    # --- bind mounts (host path; default derived from ctx at construction) ---
    data_path: Path | None = None

    # --- extra container args ---
    extra_args: list[str] = []


__all__ = ["Crawl4AiOptions"]
