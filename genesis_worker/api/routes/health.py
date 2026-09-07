"""Liveness probe. Unversioned, on purpose — load balancers don't speak /v1."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


__all__ = ["router"]
