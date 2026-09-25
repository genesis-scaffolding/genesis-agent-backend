"""Host identity + hardware snapshot — what the framework hands to plugins.

Lives in ``contracts/`` because :class:`PluginContext` carries a
``HostInfo`` instance to every plugin; the type is part of the
framework/plugin boundary, not an internal view type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ComputeDevice:
    """Base class for any device that can run llama.cpp inference.

    Each subclass declares the llama-server variant binary it needs
    and the env entry (if any) used to pin the runtime to a specific
    instance. Adding a new backend (NPU, FPGA, ...) means a new
    subclass — no edits to lookup tables. The framework calls
    :meth:`variant` and :meth:`env_entry` polymorphically.

    Frozen dataclass so pydantic can introspect ``ComputeDevice | None``
    annotations cleanly across the framework.
    """

    @property
    def variant(self) -> str:
        """The llama-server variant binary this device needs."""
        raise NotImplementedError

    def env_entry(self, binary_variant: str) -> str | None:
        """``"NAME=value"`` env entry for the child process, or ``None``.

        Returning ``None`` means the device has nothing to set for this
        binary variant (CPU is the common case; also returned for
        vendor-mismatched GPU+variant combos).
        """
        raise NotImplementedError


@dataclass(frozen=True)
class GpuDevice(ComputeDevice):
    """A discrete or integrated GPU detected on the host.

    ``vendor`` is the framework-level classification. ``index`` is
    runtime-meaningful (zero-based device index for
    ``CUDA_VISIBLE_DEVICES`` / ``GGML_VK_VISIBLE_DEVICES``); it is not
    stable across reboots or hot-plug. ``label`` is best-effort:
    NVIDIA labels come from ``nvidia-smi``; AMD/Intel get
    ``"Vulkan device {i}"`` because there is no portable enumeration
    probe shipped with the worker today.
    """

    vendor: Literal["nvidia", "amd", "intel"]
    index: int
    label: str

    @property
    def variant(self) -> str:
        if self.vendor == "nvidia":
            return "cuda"
        if self.vendor in ("amd", "intel"):
            return "vulkan"
        raise ValueError(f"unknown gpu vendor {self.vendor!r}")

    def env_entry(self, binary_variant: str) -> str | None:
        if binary_variant != self.variant:
            return None
        env_var = {
            "nvidia": "CUDA_VISIBLE_DEVICES",
            "amd": "GGML_VK_VISIBLE_DEVICES",
            "intel": "GGML_VK_VISIBLE_DEVICES",
        }[self.vendor]
        return f"{env_var}={self.index}"


@dataclass(frozen=True)
class CpuDevice(ComputeDevice):
    """The CPU as a compute device. Always available; no env vars to set.

    The collector never produces a ``CpuDevice`` — CPUs aren't
    "detected", they're a constant. Instances appear only as the
    smart-pick fallback (``smart_default_compute_device``) when no
    GPUs are detected.
    """

    @property
    def variant(self) -> str:
        return "cpu"

    def env_entry(self, binary_variant: str) -> str | None:
        return None


@dataclass(frozen=True)
class Hardware:
    """Snapshot of host GPUs and accelerators.

    Vendors are independent booleans so multi-GPU hosts (laptop with
    Intel iGPU + AMD discrete, or workstation with NVIDIA + AMD) are
    both describable. Counts are best-effort: zero when nothing is
    detected, not a measurement of installed devices.

    ``devices`` is the per-device enumeration consumed by selection
    UI (per-model device override, service-level default). It only
    contains physical devices (GpuDevice); CPU is implicit and never
    appears here. The booleans and counts stay because they are cheap
    O(1) checks consumed in many places; the list is the structured
    surface. Device order follows the collector (NVIDIA, then AMD,
    then Intel).
    """

    nvidia: bool = False
    nvidia_count: int = 0
    nvidia_driver_loaded: bool = False
    nvidia_runtime: bool = False  # legacy `--runtime nvidia` registered with the daemon
    nvidia_cdi: bool = False  # modern `--gpus all` via NVIDIA CDI specs

    amd: bool = False
    amd_count: int = 0
    amd_vendor_id_present: bool = False  # saw 0x1002 in /sys/class/drm

    intel_igpu: bool = False
    intel_count: int = 0

    devices: tuple[ComputeDevice, ...] = ()

    @classmethod
    def empty(cls) -> Hardware:
        return cls()

    def vendor_summary(self) -> str:
        """One-line dashboard summary.

        Examples: "NVIDIA (1)", "AMD (1) + Intel iGPU (1)", "none detected".
        """
        parts: list[str] = []
        if self.nvidia:
            parts.append(f"NVIDIA ({self.nvidia_count})")
        if self.amd:
            parts.append(f"AMD ({self.amd_count})")
        if self.intel_igpu:
            parts.append(f"Intel iGPU ({self.intel_count})")
        return " + ".join(parts) if parts else "none detected"


@dataclass(frozen=True)
class HostInfo:
    """Display view of the host this worker is running on."""

    hostname: str
    os: str  # e.g., "Linux 6.5.0-arch1-1"
    arch: str  # e.g., "x86_64"
    python: str  # e.g., "3.11.7"
    uptime_s: int | None
    public_ip: str | None
    tailscale_ip: str | None
    hardware: Hardware = field(default_factory=Hardware.empty)

    @classmethod
    def empty(cls) -> HostInfo:
        return cls(
            hostname="",
            os="",
            arch="",
            python="",
            uptime_s=None,
            public_ip=None,
            tailscale_ip=None,
            hardware=Hardware.empty(),
        )


__all__ = ["ComputeDevice", "CpuDevice", "GpuDevice", "Hardware", "HostInfo"]
