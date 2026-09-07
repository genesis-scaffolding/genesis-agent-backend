"""Resolved paths the worker is using. Useful for debugging "where is it
actually looking" without reaching into the dashboard's debug panel.
"""

from fastapi import APIRouter

from ..deps import WorkerDep
from ..schemas import PathsSchema

router = APIRouter()


@router.get("/paths", response_model=PathsSchema)
def get_paths(worker: WorkerDep) -> PathsSchema:
    return PathsSchema.from_settings(worker.settings.paths)


__all__ = ["router"]
