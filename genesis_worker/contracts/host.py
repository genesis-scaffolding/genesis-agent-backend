"""Host identity + hardware snapshot — what the framework hands to plugins.

Lives in ``contracts/`` because :class:`PluginContext` carries a
``HostInfo`` instance to every plugin; the type is part of the
framework/plugin boundary, not an internal view type.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Hardware:
    """Snapshot of host GPUs and accelerators.

    Vendors are independent booleans so multi-GPU hosts (laptop with
    Intel iGPU + AMD discrete, or workstation with NVIDIA + AMD) are
    both describable. Counts are best-effort: zero when nothing is
    detected, not a measurement of installed devices.
    """

    nvidia: bool = False
    nvidia_count: int = 0
    nvidia_driver_loaded: bool = False
    nvidia_runtime: bool = False  # `docker info` reports nvidia runtime

    amd: bool = False
    amd_count: int = 0
    amd_vendor_id_present: bool = False  # saw 0x1002 in /sys/class/drm

    intel_igpu: bool = False
    intel_count: int = 0

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


__all__ = ["Hardware", "HostInfo"]
