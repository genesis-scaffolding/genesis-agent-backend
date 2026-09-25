"""Host hardware detection — vendor enumeration + NVIDIA driver/runtime probes.

Single source of truth for "what does this box have for GPUs?"
Replaces the per-service ``nvidia-smi -L`` probes that lived in
``comfyui.service`` and ``llama_swap.service``; both now read off
the framework-level snapshot through ``HostInfo.hardware``.

Probes are best-effort: missing kernel modules, no ``/sys/class/drm``,
no ``nvidia-smi`` on PATH, no Docker daemon — each is reported as
"absent" rather than raised. The collectors never throw.

Detection strategy, in order:

1. NVIDIA driver loaded: ``/proc/driver/nvidia/version`` exists.
2. NVIDIA device count: ``nvidia-smi -L`` parsed for "GPU N: ..." lines.
3. AMD / Intel enumeration: ``/sys/class/drm/card*/device/vendor`` files,
   mapped from PCI vendor ID hex (``0x1002`` AMD, ``0x8086`` Intel,
   ``0x10de`` NVIDIA cross-check). Avoids needing ``lspci`` installed.
4. NVIDIA OCI runtime: ``docker info`` token match on the ``Runtimes:``
   line — NOT a substring check over the whole blob; Docker 29+ with
   only CDI installed would otherwise match ``cdi: nvidia.com/gpu=...``
   lines and falsely report a legacy runtime.
5. NVIDIA CDI: same ``docker info`` output, scanning for
   ``cdi: nvidia.com/gpu=...`` lines. This is the modern (Docker 29+)
   path; ``--gpus all`` asks the daemon to inject devices via CDI.

Probed once per process via :func:`functools.lru_cache`; the
dashboard calls ``collect_host_info`` on every render and the cost
of re-probing each time is not worth paying.
"""

from __future__ import annotations

import functools
import glob
import os
import subprocess

from ...contracts.host import GpuDevice, Hardware

_VENDOR_NVIDIA = 0x10DE
_VENDOR_AMD = 0x1002
_VENDOR_INTEL = 0x8086

_DRM_GLOB = "/sys/class/drm/card*/device/vendor"
_PROC_NVIDIA_DRIVER = "/proc/driver/nvidia/version"
_NVSMILIST_TIMEOUT_S = 5.0
_DOCKER_INFO_TIMEOUT_S = 5.0


def _read_text(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _vendor_id_for_card(card_path: str) -> int | None:
    raw = _read_text(os.path.join(card_path, "device", "vendor"))
    if raw is None:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def _enumerate_pci_vendors() -> tuple[int, int, int]:
    """Return ``(nvidia_count, amd_count, intel_count)`` from ``/sys/class/drm``.

    Only counts render nodes whose ``vendor`` file is readable.
    On hardened setups where ``/sys/class/drm`` is restricted, every
    card returns ``None`` and we silently report zero for every
    vendor — the dashboard shows "none detected" rather than a
    framework-side error.
    """
    n = a = i = 0
    for card_dir in glob.glob("/sys/class/drm/card*"):
        if not os.path.isdir(card_dir):
            continue
        vid = _vendor_id_for_card(card_dir)
        if vid is None:
            continue
        if vid == _VENDOR_NVIDIA:
            n += 1
        elif vid == _VENDOR_AMD:
            a += 1
        elif vid == _VENDOR_INTEL:
            i += 1
    return n, a, i


def _nvidia_smi_devices() -> tuple[GpuDevice, ...]:
    """Query ``nvidia-smi`` for per-device index + name.

    Returns an empty tuple on any failure (binary missing, daemon
    unreachable, timeout, non-zero exit). Lines that fail to parse
    are skipped, not raised — the collector never throws.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_NVSMILIST_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ()
    if result.returncode != 0:
        return ()
    devices: list[GpuDevice] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(",", 1)
        if len(parts) != 2:
            continue
        try:
            idx = int(parts[0].strip())
        except ValueError:
            continue
        name = parts[1].strip() or f"NVIDIA device {idx}"
        devices.append(GpuDevice(vendor="nvidia", index=idx, label=name))
    return tuple(devices)


def _pci_devices(amd_count: int, intel_count: int) -> tuple[GpuDevice, ...]:
    """Build AMD/Intel device list from PCI enumeration counts.

    Sequential per-vendor indices (``amd_count`` AMD devices indexed
    0..amd_count-1, then Intel). Labels are generic
    ``"Vulkan device {i}"`` — we do not ship a Vulkan enumeration
    probe; the operator learns the real mapping from llama.cpp errors.
    """
    devices: list[GpuDevice] = []
    for i in range(amd_count):
        devices.append(GpuDevice(vendor="amd", index=i, label=f"Vulkan device {i}"))
    for i in range(intel_count):
        devices.append(GpuDevice(vendor="intel", index=i, label=f"Vulkan device {i}"))
    return tuple(devices)


def _docker_info_lines() -> list[str] | None:
    """Lowercased, stripped lines of ``docker info``, or ``None`` on any failure.

    Shared by both runtime/CDI probes — running ``docker info`` once
    for each is wasteful on hosts where both detections need to fire.
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=_DOCKER_INFO_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return [(line or "").strip().lower() for line in (result.stdout or "").splitlines()]


def _nvidia_runtime_available(lines: list[str] | None = None) -> bool:
    """True iff ``docker info`` registers the legacy ``nvidia`` OCI runtime.

    Matches a line like ``Runtimes: io.containerd.runc.v2 runc nvidia`` —
    not the ``cdi: nvidia.com/gpu=...`` lines, which Docker 29+ exposes
    for CDI but which do NOT register a usable ``--runtime nvidia``.
    """
    if lines is None:
        lines = _docker_info_lines()
    if lines is None:
        return False
    for line in lines:
        if line.startswith("runtimes:"):
            return "nvidia" in line.split()[1:]
    return False


def _nvidia_cdi_available(lines: list[str] | None = None) -> bool:
    """True iff ``docker info`` announces an NVIDIA CDI spec.

    Docker 29+ exposes devices via CDI specs at well-known
    ``cdi: nvidia.com/gpu=...`` lines under ``Discovered Devices``.
    With CDI present the daemon can inject GPUs natively via
    ``--gpus all`` — no legacy runtime registration required.
    """
    if lines is None:
        lines = _docker_info_lines()
    if lines is None:
        return False
    for line in lines:
        if line.startswith("cdi:") and "nvidia.com/gpu=" in line:
            return True
    return False


@functools.lru_cache(maxsize=1)
def collect_hardware_info() -> Hardware:
    """One-shot hardware snapshot. Cached for the lifetime of the process."""
    nvidia_count_pci, amd_count_pci, intel_count = _enumerate_pci_vendors()
    nvidia_devices_smi = _nvidia_smi_devices()
    # Trust nvidia-smi's count when both agree on presence; otherwise
    # the higher of the two (PCI enumeration finds every card, nvidia-smi
    # only reports cards the driver is talking to). Falls back to PCI
    # count when nvidia-smi is missing.
    nvidia_count = max(nvidia_count_pci, len(nvidia_devices_smi))
    nvidia = nvidia_count > 0
    driver_loaded = os.path.exists(_PROC_NVIDIA_DRIVER)
    if nvidia:
        lines = _docker_info_lines()
        runtime = _nvidia_runtime_available(lines)
        cdi = _nvidia_cdi_available(lines)
    else:
        runtime = False
        cdi = False

    # Build the per-device list. Order: NVIDIA first (real names from
    # nvidia-smi), then PCI-detected AMD/Intel. If PCI saw more NVIDIA
    # cards than nvidia-smi enumerated, fill the gap with generic labels.
    devices: list[GpuDevice] = list(nvidia_devices_smi)
    if nvidia_count_pci > len(nvidia_devices_smi):
        for i in range(len(nvidia_devices_smi), nvidia_count_pci):
            devices.append(GpuDevice(vendor="nvidia", index=i, label=f"NVIDIA device {i}"))
    devices.extend(_pci_devices(amd_count_pci, intel_count))

    return Hardware(
        nvidia=nvidia,
        nvidia_count=nvidia_count,
        nvidia_driver_loaded=driver_loaded,
        nvidia_runtime=runtime,
        nvidia_cdi=cdi,
        amd=amd_count_pci > 0,
        amd_count=amd_count_pci,
        amd_vendor_id_present=amd_count_pci > 0,
        intel_igpu=intel_count > 0,
        intel_count=intel_count,
        devices=tuple(devices),
    )


def reset_cache() -> None:
    """Test-only: clear the lru_cache so the next call re-probes."""
    collect_hardware_info.cache_clear()


__all__ = ["collect_hardware_info", "reset_cache"]
