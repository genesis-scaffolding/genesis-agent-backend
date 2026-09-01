"""Inference service extension axis — the :class:`InferenceService` interface."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .catalog import Catalog
from .context import ServiceContext
from .install import ServiceInstall
from .plugin import Plugin
from .ui import UiPage


class ServiceState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class ServiceCategory(StrEnum):
    """Dashboard grouping. Iteration order is the visual order on the dashboard.

    New values are non-breaking — existing services keep their declared
    category, the new value just sits empty until something fills it.
    """

    LLM = "llm"
    IMAGE = "image"
    CHAT = "chat"
    CRAWLER = "crawler"
    MEDIA = "media"
    UTILITY = "utility"
    OTHER = "other"


@dataclass(frozen=True)
class ServiceCapabilities:
    """What the service can do. Drives capability-driven UI."""

    can_generate_config: bool
    can_export_for_agent: bool
    can_serve_llm: bool
    can_serve_image: bool
    can_train_models: bool
    has_web_ui: bool
    can_install: bool = False


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

    @property
    def category(self) -> ServiceCategory:
        """Dashboard grouping. Defaults to ``OTHER``; plugins must override.

        ``OTHER`` is a stopgap, not a destination — the dashboard renders
        it under a less prominent heading and the AGENTS.md plugin-author
        rule requires every new service to declare its real category.
        """
        return ServiceCategory.OTHER

    @property
    def description(self) -> str:
        """One short sentence for the Service Catalog row.

        Keep it tight (~25-30 chars) so the catalog row doesn't grow with
        verbose copy. If a service needs more, it belongs on the service's
        own landing page.
        """
        return ""

    # can_install

    def installs(self) -> list[ServiceInstall]:
        """Installables this plugin exposes. Empty list when ``can_install=False``."""
        return []

    def primary_installable(self) -> ServiceInstall | None:
        """The installable whose presence makes ``is_available()`` True, if any.

        The dashboard's one-click install button is driven by this. Default
        ``None`` — services with no install axis or where the install details
        are not yet modeled render their existing Start/Stop UI even when
        unavailable.
        """
        return None

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

    def export_for_agent(self, *, catalog: Catalog, base_url: str | None = None) -> dict:
        raise NotImplementedError(f"{self.name} does not export agent config")

    def write_agent_config(
        self, target: Path, *, catalog: Catalog, base_url: str | None = None
    ) -> bool:
        raise NotImplementedError(f"{self.name} does not export agent config")

    def agent_config_target(self) -> Path:
        raise NotImplementedError(f"{self.name} does not export agent config")

    @property
    def ui_pages(self) -> list[UiPage]:
        """Pages this service contributes. Empty list = no management UI.

        First entry is the landing page (ADR-010).
        """
        return []


__all__ = [
    "InferenceService",
    "ServiceCapabilities",
    "ServiceCategory",
    "ServiceResourceEstimate",
    "ServiceState",
    "ServiceStatus",
    "StartResult",
    "StopResult",
]
