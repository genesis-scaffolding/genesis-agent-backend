"""System metrics — CPU, RAM, GPU, VRAM. Used by the dashboard."""

from __future__ import annotations

from ..models import MachineMetrics


def collect_metrics() -> MachineMetrics:
    """Read system metrics. GPU/VRAM are None when no NVIDIA driver is reachable."""
    import psutil

    cpu = psutil.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    ram_used = float(vm.used) / (1024**3)
    ram_total = float(vm.total) / (1024**3)

    gpu_percent: float | None = None
    vram_used: float | None = None
    vram_total: float | None = None

    try:
        import pynvml  # provided by the nvidia-ml-py package now

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            gpu_percent = float(util.gpu)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            vram_used = float(mem.used) / (1024**3)
            vram_total = float(mem.total) / (1024**3)
        finally:
            pynvml.nvmlShutdown()
    except Exception:  # noqa: BLE001, S110 — no NVIDIA driver is a graceful degradation
        pass

    return MachineMetrics(
        cpu_percent=cpu,
        ram_used_gb=ram_used,
        ram_total_gb=ram_total,
        gpu_percent=gpu_percent,
        vram_used_gb=vram_used,
        vram_total_gb=vram_total,
    )


__all__ = ["collect_metrics"]
