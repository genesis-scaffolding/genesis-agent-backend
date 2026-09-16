"""Declarative service template — base classes + YAML machinery.

Phase 1 ships the concrete ``DockerService`` and ``UvService`` base
classes plus the cptr conversion. Phase 2 adds the YAML spec / loader
/ hook / panel / default-status machinery; phase 3 retires the
existing Python services in favour of YAML.

The package is a leaf under ``genesis_worker/utils`` — plugins may
import from it without violating the framework boundary (ADR-009).
"""

from .base import DeclarativeServiceBase, DeclarativeServiceConfig
from .docker_service import (
    AuthConfig,
    DockerImageInstall,
    DockerService,
    DockerServiceConfig,
)
from .loader import load_service_spec, resolve_placeholders
from .spec import (
    DockerServiceSpec,
    OptionSpec,
    ServiceSpecUnion,
    UiSpec,
    UvServiceSpec,
    spec_to_options_model,
)
from .uv_service import UvService, UvServiceConfig, UvToolInstall

__all__ = [
    "AuthConfig",
    "DeclarativeServiceBase",
    "DeclarativeServiceConfig",
    "DockerImageInstall",
    "DockerService",
    "DockerServiceConfig",
    "DockerServiceSpec",
    "OptionSpec",
    "ServiceSpecUnion",
    "UiSpec",
    "UvService",
    "UvServiceConfig",
    "UvServiceSpec",
    "UvToolInstall",
    "load_service_spec",
    "resolve_placeholders",
    "spec_to_options_model",
]
