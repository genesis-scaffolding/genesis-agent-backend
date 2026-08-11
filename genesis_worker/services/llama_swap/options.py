"""Options this service accepts under ``settings.services.llama_swap``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class LlamaSwapOptions(BaseModel):
    # Bind address for the main llama-swap process. ``0.0.0.0`` makes the
    # service reachable from other machines on the LAN/VPN, not just
    # localhost. Override via ``GENESIS_SERVICES__LLAMA_SWAP__LISTEN_ADDR``.
    listen_addr: str = "0.0.0.0:8080"
    # Hostname that *clients* (the dashboard, pi-agent) should use in URLs
    # they generate. Distinct from ``listen_addr`` because the bind address
    # is what the process listens on (``0.0.0.0``); clients can't connect to
    # ``0.0.0.0`` — they need the machine's resolvable hostname. Defaults
    # to ``socket.gethostname()``; override via the env var when the
    # machine has multiple hostnames or a private DNS one.
    public_host: str | None = None
    session_name: str = "swap"
    health_timeout_s: float = 60.0
    kv_quant_over_bytes: int = 25_000_000_000
    mmproj_offload_over_bytes: int = 25_000_000_000
    default_binary_rel: str = "vendor/llama.cpp/build/bin/llama-server"

    config_path: Path | None = None
    recipes_path: Path | None = None
    log_file: Path | None = None


__all__ = ["LlamaSwapOptions"]
