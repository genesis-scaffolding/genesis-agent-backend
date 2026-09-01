"""Tests for genesis_worker.host_info."""

from __future__ import annotations

from genesis_worker.contracts.host import HostInfo
from genesis_worker.utils.collectors.host_info import collect_host_info


def test_collect_returns_dataclass() -> None:
    info = collect_host_info()
    assert isinstance(info, HostInfo)
    assert isinstance(info.hostname, str) and info.hostname
    assert isinstance(info.os, str) and info.os
    assert isinstance(info.arch, str) and info.arch
    assert isinstance(info.python, str) and info.python
    # Hardware is a Hardware instance (always populated; may be all-False
    # on a host with no detectable GPUs).
    assert isinstance(info.hardware.vendor_summary(), str)
    # These are best-effort: a None is acceptable on systems without
    # psutil, network, or tailscale.
    if info.uptime_s is not None:
        assert info.uptime_s > 0
    if info.public_ip is not None:
        assert isinstance(info.public_ip, str)
    if info.tailscale_ip is not None:
        assert isinstance(info.tailscale_ip, str)
