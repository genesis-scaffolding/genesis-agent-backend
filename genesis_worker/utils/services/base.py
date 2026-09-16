"""Shared base class for declarative service plugins.

``DeclarativeServiceBase`` captures the cross-cutting behavior that
doesn't vary between docker and uv-tool services: identity from config,
option validation, default data/log path resolution, and the install /
uninstall / ui_pages methods whose bodies are identical across kinds.

Subclasses (``DockerService``, ``UvService``) fill in the lifecycle:
``is_available``, ``is_running``, ``start``, ``stop``, ``status``,
``wait_ready``, ``tail_log``, ``runtime_endpoint``, ``web_ui_endpoint``.

This is a base class, not a mixin — the subclass relationship is "X is a
declarative service". Naming it that way keeps the intent clear.
"""

from __future__ import annotations

import socket
from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from ...contracts import (
    InferenceService,
    ServiceCapabilities,
    ServiceContext,
    ServiceInstall,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)

ConfigT = TypeVar("ConfigT")


@dataclass(frozen=True)
class DeclarativeServiceConfig:
    """Common config fields shared by both kinds of declarative service.

    Subclass-configs add their own kind-specific fields (image vs.
    command). All declarations use dataclasses for consistency with
    the rest of ``genesis_worker/contracts``.
    """

    name: str
    display_name: str
    description: str
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate

    options_model: type[BaseModel]
    log_filename: str | None = None
    ui_pages: Sequence[str] = ()


class DeclarativeServiceBase(InferenceService, Generic[ConfigT]):
    """Service whose kind-specific bits (lifecycle, install) come from ``config``.

    The config carries everything that's uniform between DockerService
    and UvService — identity, capabilities, resource estimate, options
    schema — and subclasses fill in the docker / uv-tool specifics on
    top. The framework never reaches behind this surface.
    """

    config: ConfigT

    def __init__(self, ctx: ServiceContext, *, config: ConfigT) -> None:
        super().__init__(ctx)
        # Narrow for type-checker; the bound on ConfigT guarantees this works.
        cfg: DeclarativeServiceConfig = config  # type: ignore[assignment]
        self.config = config  # type: ignore[assignment]
        # Identity from config — YAML / Python subclasses don't set class
        # attributes for ``name`` because each instance owns its own.
        self.name = cfg.name
        self.display_name = cfg.display_name
        self.dir_name = cfg.name.replace("_", "-")
        # Validate ctx.options against the configured options model. The
        # framework has already carried the raw dict through unchanged;
        # this is the first place a typed shape is enforced. The parsed
        # model is stored so subclasses can read defaults (e.g.
        # ``public_host``).
        if not isinstance(ctx.options, dict):
            raise TypeError(f"ctx.options must be a mapping, got {type(ctx.options).__name__}")
        self.options = cfg.options_model(**ctx.options)
        self._log_filename = cfg.log_filename or f"{cfg.name}.log"

    @property
    def options_model(self) -> type[BaseModel]:
        """The pydantic model this service's options are validated against."""
        cfg: DeclarativeServiceConfig = self.config  # type: ignore[assignment]
        return cfg.options_model

    @property
    def log_file(self) -> Path:
        """Default log path under the framework-scoped ``log_dir``."""
        return self._ctx.log_dir / self._log_filename

    # --- contract: install axis (uniform across kinds) --------------------

    def installs(self) -> list[ServiceInstall]:
        return self._installs()

    def primary_installable(self) -> ServiceInstall | None:
        installables = self._installs()
        return installables[0] if installables else None

    @abstractmethod
    def _installs(self) -> list[ServiceInstall]:
        """Subclass-specific installables — one for the canonical cases."""

    def uninstall_installable(self, name: str, *, version: str | None = None) -> None:
        """Remove an installable's installed version. Refuses if the service is running.

        Mirrors the llama-swap guard so deleting the on-disk artifact
        while the process is running doesn't succeed silently.
        """
        if self.is_running():
            raise RuntimeError(
                f"cannot uninstall {name!r} while {self.display_name} is running — "
                "stop the service first"
            )
        for installable in self.installs():
            if installable.name == name:
                installable.uninstall(version=version)
                return
        raise KeyError(f"unknown installable {name!r}")

    # --- contract: ui pages (uniform across kinds) -----------------------

    @property
    def ui_pages(self) -> list[UiPage]:
        """Pages contributed by this service.

        Declarative services point at the framework's default status
        page. The page is implemented once in ``utils/services/`` (phase 2);
        for now, the base returns an empty list so existing services that
        supply their own ``ui/`` directory keep working.
        """
        return []

    # --- contract: public_host (uniform across kinds) ---------------------

    def public_host(self) -> str:
        """Hostname clients should use to reach this service.

        Reads ``public_host`` from the validated options when set;
        otherwise falls back to the host's hostname. ``listen_host``
        is a *bind* address (e.g. ``0.0.0.0``), not a connect address,
        so we don't return it directly here.
        """
        public = getattr(self.options, "public_host", None)
        if isinstance(public, str) and public:
            return public
        try:
            return socket.gethostname()
        except OSError:
            return "localhost"

    # --- contract: abstract lifecycle (subclasses fill these) ------------

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def start(self) -> StartResult: ...

    @abstractmethod
    def stop(self) -> StopResult: ...

    @abstractmethod
    def status(self) -> ServiceStatus: ...

    @abstractmethod
    def wait_ready(self, timeout_s: float) -> bool: ...

    @abstractmethod
    def tail_log(self, n_bytes: int = 8192) -> str: ...

    @abstractmethod
    def runtime_endpoint(self) -> str | None: ...

    @abstractmethod
    def web_ui_endpoint(self) -> str | None: ...


__all__ = [
    "DeclarativeServiceBase",
    "DeclarativeServiceConfig",
]
