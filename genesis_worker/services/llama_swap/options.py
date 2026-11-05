"""Options this service accepts under ``settings.services.llama_swap``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ...contracts.host import ComputeDevice
from ...utils.installables import InstallableRecord


class RadianceOptions(BaseModel):
    """Deployment and tuning defaults for the radiance engine."""

    container_port: int = Field(default=8000, gt=0)
    gpu_index: int = Field(default=0, ge=0)
    group_names: list[str] = Field(default_factory=lambda: ["render", "video"])
    tp: int = Field(default=1, ge=1)
    max_model_len: int = Field(default=32768, ge=1)
    max_num_seqs: int = Field(default=8, ge=1)
    kv_cache_dtype: Literal["bf16", "fp8", "fp8_v4", "k6_v4"] = "fp8"
    gpu_headroom_mib: int = Field(default=96, ge=0)
    ttl_s: int = Field(default=0, ge=0)
    unload_timeout_s: int = Field(default=90, ge=0)
    stop_grace_s: int = Field(default=60, ge=0)
    check_endpoint: str = "/v1/models"
    extra_docker_args: list[str] = Field(default_factory=list)
    extra_flags: list[str] = Field(default_factory=list)


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
    # explicit variant key overrides auto when the matching installable is
    # on disk. ``None`` falls back to the legacy ``default_binary_rel`` path.
    # Default is ``"auto"`` so the framework-managed binary wins without a
    # one-time setup step. Override via
    # ``GENESIS_SERVICES__LLAMA_SWAP__LLAMA_SERVER_VARIANT``.
    llama_server_variant: str | None = "auto"
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
    # Always-injected llama-server cmd flags (see generate_config's
    # DEFAULT_HARDCODED_FLAGS). ``None`` = use the framework defaults;
    # a non-empty list replaces them wholesale. Persisted through the
    # per-service JSON sidecar (list-typed, like comfyui's extra_args);
    # edited on the status page's "Server flags" section.
    hardcoded_flags: list[str] | None = None
    # Radiance engine defaults. The image itself comes from its docker_image
    # installable record rather than this option slice.
    radiance: RadianceOptions = Field(default_factory=RadianceOptions)
    # Global llama-swap health-check budget in seconds. Radiance loads large
    # models before opening its HTTP server, so this covers a multi-minute load.
    health_check_timeout_s: int = Field(default=600, ge=0)
    # User-defined installable records. Seed records are merged underneath
    # these by the service; a user record replaces the seed with the same name.
    installables: list[InstallableRecord] = Field(default_factory=list)

    config_path: Path | None = None
    recipes_path: Path | None = None
    log_file: Path | None = None


__all__ = ["LlamaSwapOptions", "RadianceOptions"]
