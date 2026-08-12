"""Tests for genesis_worker.metrics."""

from __future__ import annotations

from unittest.mock import patch

from genesis_worker.metrics import MachineMetrics, collect_metrics


def test_collect_returns_dataclass_with_cpu_ram() -> None:
    m = collect_metrics()
    assert isinstance(m, MachineMetrics)
    assert isinstance(m.cpu_percent, float)
    assert isinstance(m.ram_used_gb, float)
    assert isinstance(m.ram_total_gb, float)
    assert m.ram_total_gb > 0
    assert 0 <= m.ram_used_gb <= m.ram_total_gb + 0.01


def test_collect_handles_no_nvidia_driver() -> None:
    """When pynvml fails to init, GPU/VRAM fields are None — not an exception."""
    with patch("pynvml.nvmlInit", side_effect=OSError("no driver")):
        m = collect_metrics()
    assert m.gpu_percent is None
    assert m.vram_used_gb is None
    assert m.vram_total_gb is None
    # CPU/RAM still populated
    assert m.ram_total_gb > 0