"""Tests for the GenesisWorker facade."""

from __future__ import annotations

from pathlib import Path

from genesis_worker import GenesisWorker
from genesis_worker.contracts import (
    ServiceCapabilities,
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)
from genesis_worker.metrics import MachineMetrics


def test_facade_lists_services(tmp_path: Path) -> None:
    w = GenesisWorker()
    services = w.list_services()
    assert any(s.name == "llama_swap" for s in services)


def test_facade_returns_service_instance(tmp_path: Path) -> None:
    w = GenesisWorker()
    svc = w.service("llama_swap")
    assert svc.capabilities().can_serve_llm


def test_facade_service_status(tmp_path: Path) -> None:
    w = GenesisWorker()
    status = w.service_status("llama_swap")
    assert isinstance(status, ServiceStatus)
    assert status.state in (ServiceState.RUNNING, ServiceState.STOPPED, ServiceState.FAILED)


def test_facade_start_service_returns_start_result(tmp_path: Path) -> None:
    """Calling start when already running returns a result; we don't actually start."""
    w = GenesisWorker()
    result = w.start_service("llama_swap")
    assert isinstance(result, StartResult)


def test_facade_stop_service_returns_stop_result(tmp_path: Path) -> None:
    w = GenesisWorker()
    result = w.stop_service("llama_swap")
    assert isinstance(result, StopResult)


def test_facade_collect_metrics(tmp_path: Path) -> None:
    w = GenesisWorker()
    m = w.collect_metrics()
    assert isinstance(m, MachineMetrics)


def test_ui_pages_property_exists_with_concrete_default(tmp_path: Path) -> None:
    """Default ui_pages returns an empty list. Plugins override."""
    w = GenesisWorker()
    svc = w.service("llama_swap")
    assert isinstance(svc.ui_pages, list)
    assert all(isinstance(p, UiPage) for p in svc.ui_pages)


def test_service_capabilities_distinguishes_web_ui(tmp_path: Path) -> None:
    """has_web_ui means the service's own web UI on its native port."""
    w = GenesisWorker()
    caps = w.service("llama_swap").capabilities()
    assert isinstance(caps, ServiceCapabilities)
    # The contract documents that has_web_ui is about the service's own web UI
    # (e.g. llama-swap's :8080), not worker-managed Streamlit pages.
    assert caps.has_web_ui is True