"""FastAPI app factory. ``app`` is the uvicorn target."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .deps import get_worker
from .routes import catalog, health, host, metrics, paths, services, sessions, sources


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_worker()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Genesis Worker API",
        version="0.1.0",
        description="Read-only HTTP surface for the genesis_worker (ADR-033).",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(host.router, prefix="/v1")
    app.include_router(metrics.router, prefix="/v1")
    app.include_router(paths.router, prefix="/v1")
    app.include_router(sources.router, prefix="/v1")
    app.include_router(services.router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(sessions.router, prefix="/v1")
    return app


app = create_app()


__all__ = ["app", "create_app"]
