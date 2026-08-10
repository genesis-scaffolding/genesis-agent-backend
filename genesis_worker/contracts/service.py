"""Inference service extension axis — the :class:`InferenceService` interface."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .catalog import Catalog
from .context import ServiceContext
from .plugin import Plugin


class ServiceState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ServiceCapabilities:
    """What the service can do. Drives capability-driven UI."""

    can_generate_config: bool
    can_export_for_agent: bool
    can_serve_llm: bool
    can_serve_image: bool
    can_train_models: bool
    has_web_ui: bool


@dataclass(frozen=True)
class ServiceResourceEstimate:
    """Advisory budget — tells the dashboard what to render, not what to enforce."""

    vram_bytes_typical: int
    vram_bytes_min: int
    cpu_cores_recommended: int


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    message: str = ""
    pid: int | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class StartResult:
    ok: bool
    message: str = ""
    pid: int | None = None


@dataclass(frozen=True)
class StopResult:
    ok: bool
    message: str = ""


class InferenceService(Plugin):
    """One inference backend (llama-swap, ComfyUI, vLLM, ...).

    The methods below the lifecycle block are optional and gated by
    :meth:`capabilities`; a service that reports ``can_generate_config`` must
    implement the config group, and so on. They live here rather than on the
    concrete class so the framework only ever talks to this interface.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        self._ctx: ServiceContext = ctx

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def capabilities(self) -> ServiceCapabilities: ...

    @abstractmethod
    def resource_estimate(self) -> ServiceResourceEstimate: ...

    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def runtime_endpoint(self) -> str | None: ...

    @abstractmethod
    def start(self) -> StartResult: ...

    @abstractmethod
    def stop(self) -> StopResult: ...

    @abstractmethod
    def status(self) -> ServiceStatus: ...

    @abstractmethod
    def wait_ready(self, timeout_s: float) -> bool: ...

    # can_generate_config

    @property
    def config_path(self) -> Path:
        raise NotImplementedError(f"{self.name} does not generate config")

    def regenerate_config(self, catalog: Catalog) -> bool:
        """Rebuild the service's config from ``catalog``. True iff the file changed."""
        raise NotImplementedError(f"{self.name} does not generate config")

    def last_generated_at(self) -> str | None:
        """Catalog timestamp embedded in the current config, if any."""
        raise NotImplementedError(f"{self.name} does not generate config")

    # can_export_for_agent

    def export_for_agent(self, *, base_url: str | None = None) -> dict:
        raise NotImplementedError(f"{self.name} does not export agent config")

    def write_agent_config(self, target: Path, *, base_url: str | None = None) -> bool:
        raise NotImplementedError(f"{self.name} does not export agent config")

    def agent_config_target(self) -> Path:
        raise NotImplementedError(f"{self.name} does not export agent config")


__all__ = [
    "InferenceService",
    "ServiceCapabilities",
    "ServiceResourceEstimate",
    "ServiceState",
    "ServiceStatus",
    "StartResult",
    "StopResult",
]
