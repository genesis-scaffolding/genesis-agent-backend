"""``PortBinder`` — the per-service view of the node's port allocator.

A binder turns a port value into an integer:

- an ``int`` is its own port;
- a ``$auto_port.<name>`` marker is looked up in the allocator's registry
  (:meth:`PortBinder.allocated`) or allocated (:meth:`PortBinder.allocate`).

It also produces the ``ports=`` callable that the substitution engine
consumes: ``resolver()`` returns a ``name -> port`` callable that *allocates*
on demand, which is what the start-time materialisation pass needs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ...contracts.ports import PortAllocator
from .markers import marker_name


class PortBinder:
    """One service's handle on the node's port allocator."""

    def __init__(
        self,
        allocator: PortAllocator | None,
        service: str,
        *,
        own_containers: Callable[[], Sequence[str]] = lambda: (),
    ) -> None:
        self._allocator = allocator
        self._service = service
        self._own_containers = own_containers

    @property
    def service(self) -> str:
        return self._service

    def allocated(self, name: str) -> int | None:
        """The port recorded for one use site, or ``None``."""
        if self._allocator is None:
            return None
        return self._allocator.allocated(self._service, name)

    def allocate(self, name: str) -> int:
        """The port for one use site, allocating one if needed."""
        if self._allocator is None:
            raise RuntimeError(
                f"service {self._service!r} asks for the host port $auto_port.{name} "
                "but no port allocator is wired into this worker's service context"
            )
        return self._allocator.allocate(self._service, name, own_containers=self._own_containers())

    def resolve(self, value: int | str) -> int | None:
        """The port a value stands for, without allocating.

        An ``int`` is itself. A marker resolves to its recorded port, or
        ``None`` when the use site has never been allocated.
        """
        if isinstance(value, int):
            return value
        name = marker_name(value)
        if name is None:
            return None
        return self.allocated(name)

    def resolver(self) -> Callable[[str], int]:
        """The ``name -> port`` callable the substitution engine consumes.

        Calling it allocates the use site if it has no port yet, so this is
        only used on the start-time materialisation path.
        """
        return self.allocate


__all__ = ["PortBinder"]
