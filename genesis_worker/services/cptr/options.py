"""Options for the cptr (Open WebUI Computer) service."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

# cptr's own default stream timeouts are around 60s, which is too tight for
# local GPU inference. 1200s is the value the previous (pre-ADR-035) cptr
# lifecycle set, and matches what `docs/cptr-timeout-patch.md` recommends.
_STREAM_READ_TIMEOUT_S = 1200


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
    # Sets ``CPTR_STREAM_READ_TIMEOUT`` and ``CPTR_STREAM_WRITE_TIMEOUT`` for
    # the cptr process. Defaults to 1200s, which mirrors the pre-ADR-035
    # behavior; set to 0 to fall back to cptr's own defaults.
    # Override via ``GENESIS_SERVICES__CPTR__STREAM_TIMEOUT_S``.
    stream_timeout_s: int = _STREAM_READ_TIMEOUT_S


__all__ = ["CptrOptions"]
