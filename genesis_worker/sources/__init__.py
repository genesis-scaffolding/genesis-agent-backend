"""Model source extension axis.

Re-exports the registry helpers for ergonomic imports:
    from genesis_worker.sources import all_sources, register_source

ADR-003 details the extension architecture.
"""

from ._registry import all_sources, register_source

__all__ = ["all_sources", "register_source"]
