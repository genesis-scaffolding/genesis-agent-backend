"""Options this service accepts under ``settings.services.llama_swap``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class LlamaSwapOptions(BaseModel):
    listen_addr: str = "127.0.0.1:8080"
    session_name: str = "swap"
    health_timeout_s: float = 60.0
    kv_quant_over_bytes: int = 25_000_000_000
    mmproj_offload_over_bytes: int = 25_000_000_000
    default_binary_rel: str = "vendor/llama.cpp/build/bin/llama-server"

    config_path: Path | None = None
    recipes_path: Path | None = None
    log_file: Path | None = None


__all__ = ["LlamaSwapOptions"]
