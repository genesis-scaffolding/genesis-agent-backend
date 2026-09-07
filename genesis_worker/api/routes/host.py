"""Host identity + hardware snapshot."""

from fastapi import APIRouter

from ..deps import WorkerDep
from ..schemas import HostInfoSchema

router = APIRouter()


@router.get("/host", response_model=HostInfoSchema)
def get_host(worker: WorkerDep) -> HostInfoSchema:
    return HostInfoSchema.from_dataclass(worker.collect_host_info())


__all__ = ["router"]
