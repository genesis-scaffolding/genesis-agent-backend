"""Declarative service template — base classes for YAML-declared services.

Phase 1 ships the concrete ``DockerService`` and ``UvService`` base
classes plus the cptr conversion that proves them. Phase 2 adds the
YAML spec / loader / hook / panel machinery; phase 3 retires the
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
from .uv_service import UvService, UvServiceConfig, UvToolInstall

__all__ = [
    "AuthConfig",
    "DeclarativeServiceBase",
    "DeclarativeServiceConfig",
    "DockerImageInstall",
    "DockerService",
    "DockerServiceConfig",
    "UvService",
    "UvServiceConfig",
    "UvToolInstall",
]
