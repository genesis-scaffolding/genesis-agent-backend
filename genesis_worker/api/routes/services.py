"""Service registry view. Includes both disabled and enabled services (ADR-029).

The summary endpoint reports a stable ``state`` string per service. When
``is_available()`` is False the state is ``"unavailable"`` rather than
calling into ``status()`` which may raise on missing binaries — the
cheap gate is the source of truth for "can this service be used".

ADR-038 adds four ``POST /v1/services/{name}/{action}`` endpoints on
this router for orchestrator-driven service control (install / start /
stop / restart). The route layer translates facade exceptions to the
HTTP statuses the orchestrator contract requires: 404 for unknown
services, 409 for capability refusal, 503 for concurrent installs, 500
for start failures after materialisation.
"""

from fastapi import APIRouter, HTTPException

from ... import GenesisWorker
from ...contracts import (
    InstallInProgressError,
    ServiceCapabilityError,
)
from ..deps import WorkerDep
from ..schemas import (
    ConfigMaterialiseRequest,
    InstallResponseSchema,
    ServiceDetailSchema,
    ServiceStatusSchema,
    ServiceSummarySchema,
    StartResultSchema,
    StopResultSchema,
)

router = APIRouter()


def _state_str(worker: GenesisWorker, name: str) -> str:
    svc = worker.service(name)
    if not svc.is_available():
        return "unavailable"
    return worker.service_status(name).state.value


def _summary(worker: GenesisWorker, info) -> ServiceSummarySchema:
    svc = worker.service(info.name)
    is_running = svc.is_running()
    # ``runtime_endpoint`` is on the ABC; ``web_ui_endpoint`` is
    # duck-typed because it isn't on the contract (ADR-033 audit: all
    # five in-tree services implement it, but a future service could
    # legitimately not).
    runtime_endpoint = svc.runtime_endpoint() if is_running else None
    web_ui_getter = getattr(svc, "web_ui_endpoint", None)
    web_ui_endpoint = web_ui_getter() if (is_running and web_ui_getter is not None) else None
    return ServiceSummarySchema.from_facade(
        info,
        is_enabled=worker.services.is_enabled(info.name),
        is_available=svc.is_available(),
        state=_state_str(worker, info.name),
        runtime_endpoint=runtime_endpoint,
        web_ui_endpoint=web_ui_endpoint,
    )


@router.get("/services", response_model=list[ServiceSummarySchema])
def list_services(worker: WorkerDep) -> list[ServiceSummarySchema]:
    return [_summary(worker, info) for info in worker.list_services()]


@router.get("/services/{name}", response_model=ServiceDetailSchema)
def get_service(name: str, worker: WorkerDep) -> ServiceDetailSchema:
    for info in worker.list_services():
        if info.name != name:
            continue
        svc = worker.service(name)
        is_running = svc.is_running()
        runtime_endpoint = svc.runtime_endpoint() if is_running else None
        # ``web_ui_endpoint`` is service-specific (e.g. llama-swap adds
        # it; others don't). The dashboard uses ``getattr(..., lambda:
        # None)`` for the same reason — the ABC doesn't model it.
        web_ui_getter = getattr(svc, "web_ui_endpoint", None)
        web_ui_endpoint = web_ui_getter() if (is_running and web_ui_getter is not None) else None
        return ServiceDetailSchema.from_parts(
            info,
            is_enabled=worker.services.is_enabled(name),
            is_available=svc.is_available(),
            is_running=is_running,
            status=worker.service_status(name),
            runtime_endpoint=runtime_endpoint,
            web_ui_endpoint=web_ui_endpoint,
        )
    raise HTTPException(status_code=404, detail=f"unknown service: {name}")


@router.get("/services/{name}/status", response_model=ServiceStatusSchema)
def get_service_status(name: str, worker: WorkerDep) -> ServiceStatusSchema:
    for info in worker.list_services():
        if info.name != name:
            continue
        svc = worker.service(name)
        if not svc.is_available():
            from ...contracts import ServiceState, ServiceStatus

            return ServiceStatusSchema.from_dataclass(
                ServiceStatus(state=ServiceState.UNAVAILABLE, message="service unavailable")
            )
        status = worker.service_status(name)
        endpoint = svc.runtime_endpoint() if svc.is_running() else None
        return ServiceStatusSchema.from_dataclass(status, endpoint=endpoint)
    raise HTTPException(status_code=404, detail=f"unknown service: {name}")


# --- write endpoints (ADR-038) --------------------------------------------


@router.post(
    "/services/{name}/install",
    response_model=InstallResponseSchema,
)
def install_service(name: str, worker: WorkerDep) -> InstallResponseSchema:
    """Run the service's primary installable to completion. Idempotent.

    Errors:
        404 — ``{name}`` is not a registered service.
        409 — service has ``can_install=False`` or empty ``installs()``.
        503 — another caller is mid-install for this service.
    """
    try:
        payload = worker.install_service(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown service: {name}") from exc
    except ServiceCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InstallInProgressError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return InstallResponseSchema.from_payload(payload)


@router.post(
    "/services/{name}/start",
    response_model=StartResultSchema,
)
def start_service(
    name: str,
    worker: WorkerDep,
    body: ConfigMaterialiseRequest | None = None,
) -> StartResultSchema:
    """Start the service, optionally materialising ``config`` first.

    Errors:
        404 — ``{name}`` is not a registered service.
        409 — service cannot be started in its current state (e.g.
            binary missing for a non-install path).
        500 — start failed after materialisation. The on-disk config
            has been written; the service is stopped. The orchestrator
            surfaces ``detail`` and does not retry blindly.
    """
    config = body.config if body is not None else None
    try:
        result = worker.start_service(name, config=config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown service: {name}") from exc
    except ServiceCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Per the orchestrator's contract: 500 means the on-disk config
        # has been written and the service is stopped. Surfaces the
        # message in ``detail`` for the orchestrator to relay.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return StartResultSchema.from_dataclass(result)


@router.post(
    "/services/{name}/stop",
    response_model=StopResultSchema,
)
def stop_service(name: str, worker: WorkerDep) -> StopResultSchema:
    """Stop the service if running. Idempotent (no-op when already stopped).

    Errors:
        404 — ``{name}`` is not a registered service.
    """
    try:
        result = worker.stop_service(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown service: {name}") from exc
    return StopResultSchema.from_dataclass(result)


@router.post(
    "/services/{name}/restart",
    response_model=StartResultSchema,
)
def restart_service(
    name: str,
    worker: WorkerDep,
    body: ConfigMaterialiseRequest | None = None,
) -> StartResultSchema:
    """Convenience for stop + start with optional config. Same error semantics as ``start``.

    Errors:
        404 — ``{name}`` is not a registered service.
        409 — as ``start``.
        500 — as ``start``.
    """
    config = body.config if body is not None else None
    try:
        result = worker.restart_service(name, config=config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown service: {name}") from exc
    except ServiceCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return StartResultSchema.from_dataclass(result)


__all__ = ["router"]
