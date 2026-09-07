"""Live system metrics (CPU, RAM, GPU, VRAM)."""

from fastapi import APIRouter

from ..deps import WorkerDep
from ..schemas import MetricsSchema

router = APIRouter()


@router.get("/metrics", response_model=MetricsSchema)
def get_metrics(worker: WorkerDep) -> MetricsSchema:
    return MetricsSchema.from_dataclass(worker.collect_metrics())


__all__ = ["router"]
