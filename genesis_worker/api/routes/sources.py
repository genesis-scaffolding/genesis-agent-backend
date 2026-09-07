"""Source registry view."""

from fastapi import APIRouter, HTTPException

from ... import GenesisWorker
from ..deps import WorkerDep
from ..schemas import SourceSummarySchema

router = APIRouter()


def _build(worker: GenesisWorker, info) -> SourceSummarySchema:
    entries = worker.catalog().by_source().get(info.name, [])
    total_bytes = sum(e.total_bytes for e in entries)
    return SourceSummarySchema.from_parts(
        info,
        local_path=str(worker.source(info.name).local_path),
        model_count=len(entries),
        total_bytes=total_bytes,
    )


@router.get("/sources", response_model=list[SourceSummarySchema])
def list_sources(worker: WorkerDep) -> list[SourceSummarySchema]:
    return [_build(worker, info) for info in worker.list_sources()]


@router.get("/sources/{name}", response_model=SourceSummarySchema)
def get_source(name: str, worker: WorkerDep) -> SourceSummarySchema:
    for info in worker.list_sources():
        if info.name == name:
            return _build(worker, info)
    raise HTTPException(status_code=404, detail=f"unknown source: {name}")


__all__ = ["router"]
