"""Pydantic response models for the API. Decoupled from contract types so the
JSON shape is stable and can extend beyond what a plugin sees.

Every model that wraps a contract dataclass or facade view type carries
a ``from_*`` classmethod that does the conversion. Schema fields are
explicit (no ``**kwargs`` spread) so OpenAPI generation is precise.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..contracts import (
    AcquireProgress,
    AcquireView,
    Catalog,
    Hardware,
    HostInfo,
    ModelEntry,
    ModelPiece,
    ServiceCapabilities,
    ServiceStatus,
)
from ..utils.models import MachineMetrics, ServiceInfo
from ..utils.models import SourceInfo as FacadeSourceInfo


class HardwareSchema(BaseModel):
    nvidia: bool
    nvidia_count: int
    nvidia_driver_loaded: bool
    nvidia_runtime: bool
    amd: bool
    amd_count: int
    amd_vendor_id_present: bool
    intel_igpu: bool
    intel_count: int

    @classmethod
    def from_dataclass(cls, h: Hardware) -> HardwareSchema:
        return cls(
            nvidia=h.nvidia,
            nvidia_count=h.nvidia_count,
            nvidia_driver_loaded=h.nvidia_driver_loaded,
            nvidia_runtime=h.nvidia_runtime,
            amd=h.amd,
            amd_count=h.amd_count,
            amd_vendor_id_present=h.amd_vendor_id_present,
            intel_igpu=h.intel_igpu,
            intel_count=h.intel_count,
        )


class HostInfoSchema(BaseModel):
    hostname: str
    os: str
    arch: str
    python: str
    uptime_s: int | None
    public_ip: str | None
    tailscale_ip: str | None
    hardware: HardwareSchema

    @classmethod
    def from_dataclass(cls, h: HostInfo) -> HostInfoSchema:
        return cls(
            hostname=h.hostname,
            os=h.os,
            arch=h.arch,
            python=h.python,
            uptime_s=h.uptime_s,
            public_ip=h.public_ip,
            tailscale_ip=h.tailscale_ip,
            hardware=HardwareSchema.from_dataclass(h.hardware),
        )


class MetricsSchema(BaseModel):
    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    gpu_percent: float | None
    vram_used_gb: float | None
    vram_total_gb: float | None

    @classmethod
    def from_dataclass(cls, m: MachineMetrics) -> MetricsSchema:
        return cls(
            cpu_percent=m.cpu_percent,
            ram_used_gb=m.ram_used_gb,
            ram_total_gb=m.ram_total_gb,
            gpu_percent=m.gpu_percent,
            vram_used_gb=m.vram_used_gb,
            vram_total_gb=m.vram_total_gb,
        )


class PathsSchema(BaseModel):
    vault: str
    data_dir: str
    config_dir: str
    cache_dir: str
    state_dir: str
    log_dir: str
    repo_root: str

    @classmethod
    def from_settings(cls, paths: Any) -> PathsSchema:
        return cls(
            vault=str(paths.resolved_vault_path),
            data_dir=str(paths.data_dir),
            config_dir=str(paths.config_dir),
            cache_dir=str(paths.cache_dir),
            state_dir=str(paths.state_dir),
            log_dir=str(paths.log_dir),
            repo_root=str(paths.resolved_repo_root),
        )


class ServiceCapabilitiesSchema(BaseModel):
    can_generate_config: bool
    can_export_for_agent: bool
    can_serve_llm: bool
    can_serve_image: bool
    can_train_models: bool
    has_web_ui: bool
    can_install: bool

    @classmethod
    def from_dataclass(cls, c: ServiceCapabilities) -> ServiceCapabilitiesSchema:
        return cls(
            can_generate_config=c.can_generate_config,
            can_export_for_agent=c.can_export_for_agent,
            can_serve_llm=c.can_serve_llm,
            can_serve_image=c.can_serve_image,
            can_train_models=c.can_train_models,
            has_web_ui=c.has_web_ui,
            can_install=c.can_install,
        )


class ServiceStatusSchema(BaseModel):
    state: str
    message: str
    pid: int | None
    endpoint: str | None

    @classmethod
    def from_dataclass(
        cls, s: ServiceStatus, *, endpoint: str | None = None
    ) -> ServiceStatusSchema:
        return cls(
            state=s.state.value,
            message=s.message,
            pid=s.pid,
            endpoint=endpoint,
        )


class ServiceSummarySchema(BaseModel):
    """List-row shape — small enough for tables, but already includes
    the runtime/web URLs so a downstream consumer can build a "what's
    running and where" view without N+1 calls into the detail route.

    Both URL fields are ``None`` when the service is not running; the
    methods themselves guard on ``is_running()`` so this is the
    natural shape.
    """

    name: str
    display_name: str
    description: str
    category: str
    is_enabled: bool
    is_available: bool
    state: str
    runtime_endpoint: str | None
    web_ui_endpoint: str | None

    @classmethod
    def from_facade(
        cls,
        info: ServiceInfo,
        *,
        is_enabled: bool,
        is_available: bool,
        state: str,
        runtime_endpoint: str | None,
        web_ui_endpoint: str | None,
    ) -> ServiceSummarySchema:
        return cls(
            name=info.name,
            display_name=info.display_name,
            description=info.description,
            category=info.category.value,
            is_enabled=is_enabled,
            is_available=is_available,
            state=state,
            runtime_endpoint=runtime_endpoint,
            web_ui_endpoint=web_ui_endpoint,
        )


class ServiceDetailSchema(BaseModel):
    """Full single-service shape — includes status, capabilities, endpoints."""

    name: str
    display_name: str
    description: str
    category: str
    is_enabled: bool
    is_available: bool
    is_running: bool
    capabilities: ServiceCapabilitiesSchema
    status: ServiceStatusSchema
    runtime_endpoint: str | None
    web_ui_endpoint: str | None

    @classmethod
    def from_parts(
        cls,
        info: ServiceInfo,
        *,
        is_enabled: bool,
        is_available: bool,
        is_running: bool,
        status: ServiceStatus,
        runtime_endpoint: str | None,
        web_ui_endpoint: str | None,
    ) -> ServiceDetailSchema:
        return cls(
            name=info.name,
            display_name=info.display_name,
            description=info.description,
            category=info.category.value,
            is_enabled=is_enabled,
            is_available=is_available,
            is_running=is_running,
            capabilities=ServiceCapabilitiesSchema.from_dataclass(info.capabilities),
            status=ServiceStatusSchema.from_dataclass(status, endpoint=runtime_endpoint),
            runtime_endpoint=runtime_endpoint,
            web_ui_endpoint=web_ui_endpoint,
        )


class SourceSummarySchema(BaseModel):
    name: str
    display_name: str
    can_acquire: bool
    is_available: bool
    local_path: str
    model_count: int
    total_bytes: int

    @classmethod
    def from_parts(
        cls,
        info: FacadeSourceInfo,
        *,
        local_path: str,
        model_count: int,
        total_bytes: int,
    ) -> SourceSummarySchema:
        return cls(
            name=info.name,
            display_name=info.display_name,
            can_acquire=info.can_acquire,
            is_available=info.is_available,
            local_path=local_path,
            model_count=model_count,
            total_bytes=total_bytes,
        )


class ModelPieceSchema(BaseModel):
    role: str
    filename: str
    path: str
    bytes: int

    @classmethod
    def from_dataclass(cls, p: ModelPiece) -> ModelPieceSchema:
        return cls(
            role=p.role,
            filename=p.filename,
            path=str(p.path),
            bytes=p.bytes,
        )


class ModelEntrySchema(BaseModel):
    name: str
    source: str
    pieces: list[ModelPieceSchema]
    total_bytes: int
    directory: str
    notes: list[str]
    extra: dict

    @classmethod
    def from_dataclass(cls, e: ModelEntry) -> ModelEntrySchema:
        return cls(
            name=e.name,
            source=e.source,
            pieces=[ModelPieceSchema.from_dataclass(p) for p in e.pieces],
            total_bytes=e.total_bytes,
            directory=e.directory,
            notes=list(e.notes),
            extra=dict(e.extra),
        )


class CatalogSchema(BaseModel):
    schema_version: int
    root: str
    generated_at: str
    content_hash: str
    entries: list[ModelEntrySchema]

    @classmethod
    def from_dataclass(cls, c: Catalog) -> CatalogSchema:
        return cls(
            schema_version=c.schema_version,
            root=c.root,
            generated_at=c.generated_at,
            content_hash=c.content_hash,
            entries=[ModelEntrySchema.from_dataclass(e) for e in c.entries],
        )


class CatalogBySourceSchema(BaseModel):
    """Same content as the catalog, grouped for clients that want per-source lists."""

    generated_at: str
    content_hash: str
    by_source: dict[str, list[ModelEntrySchema]]


class AcquireProgressSchema(BaseModel):
    bytes_done: int
    bytes_total: int
    speed_bps: int
    eta_s: int

    @classmethod
    def from_dataclass(cls, p: AcquireProgress) -> AcquireProgressSchema:
        return cls(
            bytes_done=p.bytes_done,
            bytes_total=p.bytes_total,
            speed_bps=p.speed_bps,
            eta_s=p.eta_s,
        )


class AcquireViewSchema(BaseModel):
    kind: str
    title: str
    prompt: str | None
    progress: AcquireProgressSchema | None
    log_tail: list[str] | None
    can_cancel: bool
    error: str | None
    cache_dir: str | None
    total_bytes: int | None

    @classmethod
    def from_dataclass(cls, v: AcquireView) -> AcquireViewSchema:
        return cls(
            kind=v.kind.value,
            title=v.title,
            prompt=v.prompt,
            progress=AcquireProgressSchema.from_dataclass(v.progress) if v.progress else None,
            log_tail=list(v.log_tail) if v.log_tail else None,
            can_cancel=v.can_cancel,
            error=v.error,
            cache_dir=str(v.cache_dir) if v.cache_dir else None,
            total_bytes=v.total_bytes,
        )


class SessionSummarySchema(BaseModel):
    id: str
    source: str
    repo_id: str
    state: str


__all__ = [
    "AcquireProgressSchema",
    "AcquireViewSchema",
    "CatalogBySourceSchema",
    "CatalogSchema",
    "HardwareSchema",
    "HostInfoSchema",
    "MetricsSchema",
    "ModelEntrySchema",
    "ModelPieceSchema",
    "PathsSchema",
    "ServiceCapabilitiesSchema",
    "ServiceDetailSchema",
    "ServiceStatusSchema",
    "ServiceSummarySchema",
    "SessionSummarySchema",
    "SourceSummarySchema",
]
