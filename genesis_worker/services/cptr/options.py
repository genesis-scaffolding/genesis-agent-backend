"""Options for the cptr (Open WebUI Computer) service."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class CptrOptions(BaseModel):
    # Bind address for the cptr process. ``0.0.0.0`` exposes it on the LAN/VPN.
    # Override via ``GENESIS_SERVICES__CPTR__LISTEN_HOST``.
    listen_host: str = "0.0.0.0"
    # cptr's own default is 8000; we pin 4321 per house convention.
    # Override via ``GENESIS_SERVICES__CPTR__LISTEN_PORT``.
    listen_port: int = 4321
    # Hostname that *clients* (the dashboard) should use in URLs they generate.
    # Distinct from ``listen_host`` (``0.0.0.0`` is a bind address, not a
    # connect address). Defaults to ``socket.gethostname()``.
    public_host: str | None = None
    session_name: str = "cptr"
    health_timeout_s: float = 60.0
    log_file: Path | None = None


__all__ = ["CptrOptions"]
