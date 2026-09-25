"""Options this service accepts under ``settings.services.llama_swap``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ...contracts.host import ComputeDevice


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
    # Which framework-managed llama-server binary the config generator
    # should use as the default. ``"auto"`` picks via hardware detection:
    # NVIDIA GPU + cuda installed → cuda; else vulkan; else cpu. The
    # explicit ``"cuda" / "cpu" / "vulkan"`` overrides auto when the
    # matching installable is on disk. ``None`` falls back to the legacy
    # ``default_binary_rel`` path. Default is ``"auto"`` so the framework-
    # managed binary wins without a one-time setup step. Override via
    # ``GENESIS_SERVICES__LLAMA_SWAP__LLAMA_SERVER_VARIANT``.
    llama_server_variant: Literal["auto", "cuda", "cpu", "vulkan"] | None = "auto"
    # Pin a specific compute device. ``None`` falls back to the smart
    # auto-pick (NVIDIA 0 → AMD 0 → Intel 0 → CPU). When set, the
    # matching variant binary is required — the service fails loud at
    # config-regen time if the binary is missing or the vendor has no
    # framework-managed variant. Persistence follows the standard
    # flat-key path under ``Settings.services.llama_swap``; pydantic
    # serializes ``ComputeDevice`` polymorphically by type (GpuDevice
    # → ``{vendor, index, label}``, CpuDevice → ``{}``). Override via
    # ``GENESIS_SERVICES__LLAMA_SWAP__DEFAULT_DEVICE``.
    default_device: ComputeDevice | None = None

    config_path: Path | None = None
    recipes_path: Path | None = None
    log_file: Path | None = None


__all__ = ["LlamaSwapOptions"]
