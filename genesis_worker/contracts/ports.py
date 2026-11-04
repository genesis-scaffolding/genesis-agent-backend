"""The framework's service-extension axis — the ``PortAllocator`` protocol.

A plugin (or the framework's own declarative services) that needs a host port
allocated by the node-level allocator talks to it through this protocol. The
implementation lives in ``genesis_worker.utils.ports.allocator``; the contract
lives here so ``contracts/`` stays free of any ``utils`` import (ADR-009).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class PortAllocator(Protocol):
    """Allocates host ports from a node-level range, persistently.

    One allocator instance belongs to one worker process; the state lives in a
    file so a second process (or a restarted worker) sees the same
    allocations.
    """

    def allocate(
        self,
        service: str,
        name: str,
        *,
        own_containers: Sequence[str] = (),
    ) -> int:
        """The host port for ``service``'s use site ``name``.

        Allocates one if the use site has none, and reuses the recorded port
        when it is still free for this service. ``own_containers`` names the
        containers the calling service owns — a port published by one of them
        counts as free for that service.
        """
        ...

    def allocated(self, service: str, name: str) -> int | None:
        """The recorded port for one use site, or ``None`` when it has none."""
        ...

    def release(self, service: str, name: str) -> None:
        """Drop the record for one use site. The port becomes available again."""
        ...

    def snapshot(self) -> Mapping[str, Mapping[str, int]]:
        """The whole registry: service -> use-site name -> port."""
        ...


__all__ = ["PortAllocator"]
