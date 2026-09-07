"""Worker singleton and FastAPI dependency.

The API process holds its own ``GenesisWorker``. The lifespan in
``app.py`` calls :func:`get_worker` once at startup so plugin
construction errors surface eagerly rather than on the first
request; routes use the ``WorkerDep`` annotation below to get the
cached singleton per request.

``WorkerDep`` is declared with :class:`typing.Annotated` rather than
``field: T = Depends(...)`` in defaults — the former avoids a
``B008`` lint warning and is FastAPI's recommended pattern for
dependencies used in many routes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from .. import GenesisWorker

_worker: GenesisWorker | None = None


def get_worker() -> GenesisWorker:
    """Return the process-level worker, constructing it on first call."""
    global _worker
    if _worker is None:
        _worker = GenesisWorker()
    return _worker


def reset_worker() -> None:
    """Test-only: drop the cached singleton so the next ``get_worker``
    call rebuilds it. The boundary test walks ``sources/`` and
    ``services/``; this helper is used by ``test_api.py`` to hermeticise.
    """
    global _worker
    _worker = None


WorkerDep = Annotated[GenesisWorker, Depends(get_worker)]


__all__ = ["WorkerDep", "get_worker", "reset_worker"]
