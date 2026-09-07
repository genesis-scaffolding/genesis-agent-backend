"""Acquire session registry view. Sessions are process-local (ADR-033):
this process only sees sessions it started itself.
"""

from fastapi import APIRouter, HTTPException

from ..deps import WorkerDep
from ..schemas import AcquireViewSchema, SessionSummarySchema

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummarySchema])
def list_sessions(worker: WorkerDep) -> list[SessionSummarySchema]:
    return [
        SessionSummarySchema(
            id=entry["id"],
            source=entry["source"],
            repo_id=entry["repo_id"],
            state=entry["state"],
        )
        for entry in worker.list_acquire_sessions()
    ]


@router.get("/sessions/{session_id}", response_model=AcquireViewSchema)
def get_session(session_id: str, worker: WorkerDep) -> AcquireViewSchema:
    for entry in worker.list_acquire_sessions():
        if entry["id"] == session_id:
            return AcquireViewSchema.from_dataclass(worker.acquire_step(entry["session"]))
    raise HTTPException(status_code=404, detail=f"no session {session_id}")


__all__ = ["router"]
