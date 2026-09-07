"""Service registry view. Includes both disabled and enabled services (ADR-029).

The summary endpoint reports a stable ``state`` string per service. When
``is_available()`` is False the state is ``"unavailable"`` rather than
calling into ``status()`` which may raise on missing binaries — the
cheap gate is the source of truth for "can this service be used".
"""

from fastapi import APIRouter, HTTPException

from ... import GenesisWorker
from ..deps import WorkerDep
from ..schemas import ServiceDetailSchema, ServiceStatusSchema, ServiceSummarySchema

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


__all__ = ["router"]
