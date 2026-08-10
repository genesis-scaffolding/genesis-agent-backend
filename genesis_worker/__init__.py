"""Genesis Worker — the importable Python package.

The :class:`GenesisWorker` facade is the single public entry point for
CLI, Streamlit, and external consumers. Tests and CLI scripts typically
do::

    from genesis_worker import GenesisWorker

    worker = GenesisWorker()
    for info in worker.list_sources():
        ...
    for info in worker.list_services():
        ...
    catalog = worker.rescan_catalog()

Submodules (``catalog``, ``services``, ``sources``, ``models``,
``settings``, ``paths``) are reachable directly but should be reached
through the facade in consumer code.

ADR-003 details the facade rationale.
"""

from .facade import GenesisWorker, ServiceInfo, SourceInfo

__all__ = ["GenesisWorker", "ServiceInfo", "SourceInfo"]
