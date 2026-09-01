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
4. NVIDIA runtime: ``docker info`` substring match (same probe
   ``DockerContainer.nvidia_runtime_available`` performs today;
   both surfaces agree on the answer).

Probed once per process via :func:`functools.lru_cache`; the
dashboard calls ``collect_host_info`` on every render and the cost
of re-probing each time is not worth paying.
"""

from __future__ import annotations

import functools
import glob
import os
import subprocess

from ...contracts.host import Hardware

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


def _nvidia_smi_count() -> int:
    """Run ``nvidia-smi -L`` and count the GPU entries.

    Returns 0 on any failure (binary missing, daemon unreachable,
    timeout, non-zero exit). The string ``"GPU "`` is the standard
    prefix on each list line; counting occurrences is more robust
    than splitting lines because some drivers emit extra blank lines.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=_NVSMILIST_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 0
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.startswith("GPU "))


def _nvidia_runtime_available() -> bool:
    """True iff ``docker info`` reports the nvidia runtime."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=_DOCKER_INFO_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    return "nvidia" in (result.stdout or "").lower()


@functools.lru_cache(maxsize=1)
def collect_hardware_info() -> Hardware:
    """One-shot hardware snapshot. Cached for the lifetime of the process."""
    nvidia_count_pci, amd_count_pci, intel_count = _enumerate_pci_vendors()
    nvidia_count_smi = _nvidia_smi_count()
    # Trust nvidia-smi's count when both agree on presence; otherwise
    # the higher of the two (PCI enumeration finds every card, nvidia-smi
    # only reports cards the driver is talking to). Falls back to PCI
    # count when nvidia-smi is missing.
    nvidia_count = max(nvidia_count_pci, nvidia_count_smi)
    nvidia = nvidia_count > 0
    driver_loaded = os.path.exists(_PROC_NVIDIA_DRIVER)
    runtime = _nvidia_runtime_available() if nvidia else False
    return Hardware(
        nvidia=nvidia,
        nvidia_count=nvidia_count,
        nvidia_driver_loaded=driver_loaded,
        nvidia_runtime=runtime,
        amd=amd_count_pci > 0,
        amd_count=amd_count_pci,
        amd_vendor_id_present=amd_count_pci > 0,
        intel_igpu=intel_count > 0,
        intel_count=intel_count,
    )


def reset_cache() -> None:
    """Test-only: clear the lru_cache so the next call re-probes."""
    collect_hardware_info.cache_clear()


__all__ = ["collect_hardware_info", "reset_cache"]
