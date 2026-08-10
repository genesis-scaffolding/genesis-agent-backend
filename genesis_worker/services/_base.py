"""Inference service extension axis — Protocol + dataclasses.

A :class:`InferenceService` is the lifecycle façade for one inference
backend: llama-swap today, ComfyUI / AIToolkit / vLLM tomorrow. Each
service has its own binary, port, config-file format, lifecycle, and
(sometimes) its own concept of "recipe" or "preset."

The framework constructs each service with its per-service settings
slice via :class:`~genesis_worker.services._registry.ServiceRegistry`.
Concrete services implement :class:`InferenceService` and expose their
capabilities / status / lifecycle hooks through the Protocol.

Status, capability, and result types live here at the axis level so
any consumer (UI, CLI, facade) can reason about services without
importing a concrete implementation.

ADR-003 details the extension architecture. Lifecycle plumbing
(tmux, curl, process supervision) for individual services lands in
plan-002; this module defines the contract those implementations
will satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ServiceState(StrEnum):
    """Coarse lifecycle state for an inference service."""

    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ServiceCapabilities:
    """What the service can do. Drives capability-driven UI (no hardcoded
    ``if service == "llama-swap"`` branches in the dashboard)."""

    can_generate_config: bool
    can_export_for_agent: bool
    can_serve_llm: bool
    can_serve_image: bool
    can_train_models: bool
    has_web_ui: bool


@dataclass(frozen=True)
class ServiceResourceEstimate:
    """Rough resource budget for running this service. Values are advisory —
    they tell the dashboard what tiles to render, not what to enforce."""

    vram_bytes_typical: int
    vram_bytes_min: int
    cpu_cores_recommended: int


@dataclass(frozen=True)
class ServiceStatus:
    """Current runtime state of a service."""

    state: ServiceState
    message: str = ""
    pid: int | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class StartResult:
    """Outcome of a start() call."""

    ok: bool
    message: str = ""
    pid: int | None = None


@dataclass(frozen=True)
class StopResult:
    """Outcome of a stop() call."""

    ok: bool
    message: str = ""


@runtime_checkable
class InferenceService(Protocol):
    """One inference backend (llama-swap, ComfyUI, AIToolkit, vLLM, ...).

    Concrete services declare:

    - ``name``: short identifier (``"llama_swap"``).
    - ``display_name``: human-readable name for UI.

    The framework constructs each service with its per-service settings
    slice via :class:`ServiceRegistry`. Services do not import settings
    machinery directly; they receive what they need at construction.

    Lifecycle methods (``start`` / ``stop`` / ``status`` / ``is_running``
    / ``runtime_endpoint`` / ``wait_ready`` / ``resource_estimate``)
    involve tmux / curl / process supervision and land in plan-002 for
    each concrete service. This Protocol declares the contract; the
    implementations fill it in.
    """

    name: str
    display_name: str

    def is_available(self) -> bool: ...
    def is_running(self) -> bool: ...
    def runtime_endpoint(self) -> str | None: ...
    def capabilities(self) -> ServiceCapabilities: ...
    def resource_estimate(self) -> ServiceResourceEstimate: ...
    def start(self) -> StartResult: ...
    def stop(self) -> StopResult: ...
    def status(self) -> ServiceStatus: ...
    def wait_ready(self, timeout_s: float) -> bool: ...


__all__ = [
    "InferenceService",
    "ServiceCapabilities",
    "ServiceResourceEstimate",
    "ServiceState",
    "ServiceStatus",
    "StartResult",
    "StopResult",
]
