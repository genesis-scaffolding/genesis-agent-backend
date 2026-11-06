"""The node-level host-port allocator.

One allocator per worker process. It owns a small registry file
(``<state_dir>/port_allocations.yaml``) that maps a service and a use-site
name to the host port that was allocated for it::

    allocations:
      metube:
        listen: 18000
      genesis-productivity:
        listen: 18001
        api: 18002

The registry is what makes an allocation survive a worker restart: the
container keeps running across the restart, and the framework's view of its
port has to match.

A record is written by the start path (:meth:`NodePortAllocator.allocate`)
or adopted from a running container that already publishes the port
(:meth:`NodePortAllocator.adopt`, ADR-045).

**Two processes, one node.** The worker runs a Streamlit UI process and a
FastAPI API process; both can start a service. Every mutation therefore runs
inside an exclusive ``fcntl.flock`` *and* re-reads the file from disk inside
the lock — locking alone is not enough, because the other process's writes
are only visible after a fresh read.

The lock lives on its own file (``port_allocations.lock``), not on the
registry file: the registry is written atomically (sibling ``.tmp`` +
``os.replace``), which swaps the inode, and a lock held on the old inode
would not exclude the next writer.

**What "free" means.** A candidate port is free for the service asking for
it only if all of the following hold (ADR-043 §5):

1. no other service (and no other use site of this service) holds it in the
   registry;
2. it is not a fixed port of a registered service nor a framework port;
3. it is not declared by a container that is not ours;
4. it is bindable right now — unless one of *our own* containers already
   publishes it, which is what makes the sticky reuse work for a running
   compose stack.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from .environment import ContainerPortBinding, bindable, container_port_bindings, framework_ports

# The node-level range for dynamically allocated host ports. Outside the
# Linux ephemeral range (32768-60999), so the kernel never hands one of
# these to an outbound connection.
DYNAMIC_PORT_RANGE = (18000, 18999)

_STATE_FILE = "port_allocations.yaml"
_LOCK_FILE = "port_allocations.lock"
_FILE_MODE = 0o600


class NodePortAllocator:
    """Allocates host ports from a node-level range and remembers them."""

    def __init__(
        self,
        state_dir: Path,
        *,
        fixed_ports: Callable[[], Iterable[int]] | None = None,
        container_bindings: Callable[[], Iterable[ContainerPortBinding]] | None = None,
        bind_probe: Callable[[int], bool] | None = None,
        port_range: tuple[int, int] = DYNAMIC_PORT_RANGE,
    ) -> None:
        self._state_dir = state_dir
        self._fixed_ports = fixed_ports if fixed_ports is not None else framework_ports
        self._container_bindings = (
            container_bindings if container_bindings is not None else container_port_bindings
        )
        self._bind_probe = bind_probe if bind_probe is not None else bindable
        self._port_range = port_range

    # --- public API -------------------------------------------------------

    def allocate(
        self,
        service: str,
        name: str,
        *,
        own_containers: Sequence[str] = (),
    ) -> int:
        """The host port for ``service``'s use site ``name``.

        Sticky: the recorded port is reused when it is still free for this
        service. Otherwise the range is walked in order and the first free
        port is recorded.
        """
        with self._locked():
            registry = self._read_registry()
            recorded = registry.get(service, {}).get(name)
            if recorded is not None and self._is_free(
                recorded, registry, service, name, own_containers
            ):
                return recorded
            for port in range(self._port_range[0], self._port_range[1] + 1):
                if port == recorded:
                    continue
                if not self._is_free(port, registry, service, name, own_containers):
                    continue
                registry.setdefault(service, {})[name] = port
                self._write_registry(registry)
                return port
        raise RuntimeError(
            f"the dynamic host-port range {self._port_range[0]}-{self._port_range[1]} "
            f"is exhausted; no free port for {service!r} use site {name!r}"
        )

    def adopt(self, service: str, name: str, port: int) -> bool:
        """Record the port ``service``'s own container already publishes.

        The read-side sibling of :meth:`allocate`: a running container is
        the truth about the port it holds (ADR-045). Idempotent — a use
        site that already has a record keeps it — and it refuses a port
        another service holds. No bind probe: the port is not a
        candidate, it is already published by our own container.
        """
        with self._locked():
            registry = self._read_registry()
            if registry.get(service, {}).get(name) is not None:
                return False
            for other_service, records in registry.items():
                for other_name, other_port in records.items():
                    if other_port != port:
                        continue
                    if other_service == service and other_name == name:
                        continue
                    return False
            if port in set(self._fixed_ports()):
                return False
            registry.setdefault(service, {})[name] = port
            self._write_registry(registry)
            return True

    def allocated(self, service: str, name: str) -> int | None:
        """The recorded port for one use site, or ``None`` when it has none."""
        registry = self._read_registry()
        return registry.get(service, {}).get(name)

    def release(self, service: str, name: str) -> None:
        """Drop the record for one use site. The port becomes available again."""
        with self._locked():
            registry = self._read_registry()
            if name not in registry.get(service, {}):
                return
            del registry[service][name]
            if not registry[service]:
                del registry[service]
            self._write_registry(registry)

    def reconcile(self, service: str, keep: Iterable[str]) -> None:
        """Drop every record of ``service`` whose use-site name is not in ``keep``.

        Called after a spec rebuild: a service that no longer declares a
        marker (the user pinned a fixed port instead) must not keep a stale
        record, or the range leaks slowly.
        """
        keep_set = set(keep)
        with self._locked():
            registry = self._read_registry()
            records = registry.get(service)
            if not records:
                return
            stale = [name for name in records if name not in keep_set]
            if not stale:
                return
            for name in stale:
                del records[name]
            if not records:
                del registry[service]
            self._write_registry(registry)

    def snapshot(self) -> Mapping[str, Mapping[str, int]]:
        """The whole registry: service -> use-site name -> port."""
        return self._read_registry()

    # --- the free check ----------------------------------------------------

    def _is_free(
        self,
        port: int,
        registry: Mapping[str, Mapping[str, int]],
        service: str,
        name: str,
        own_containers: Sequence[str],
    ) -> bool:
        """Whether ``port`` may be allocated to ``service``'s use site ``name``."""
        for other_service, records in registry.items():
            for other_name, other_port in records.items():
                if other_port != port:
                    continue
                if other_service == service and other_name == name:
                    continue  # our own record — the sticky candidate
                return False
        if port in set(self._fixed_ports()):
            return False
        bindings = list(self._container_bindings())
        ours = [b for b in bindings if _is_own(b, own_containers)]
        theirs = [b for b in bindings if not _is_own(b, own_containers)]
        if any(port in b.host_ports for b in theirs):
            return False
        if any(port in b.host_ports for b in ours):
            return True  # our own container already publishes it
        return self._bind_probe(port)

    # --- persistence --------------------------------------------------------

    @property
    def state_path(self) -> Path:
        """The registry file."""
        return self._state_dir / _STATE_FILE

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the node's allocation lock for the duration of the block."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._state_dir / _LOCK_FILE
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, _FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_registry(self) -> dict[str, dict[str, int]]:
        """Read the registry from disk. A corrupt file reads as empty."""
        try:
            text = self.state_path.read_text()
        except (FileNotFoundError, OSError):
            return {}
        try:
            data: Any = yaml.safe_load(text)
        except yaml.YAMLError:
            return {}
        if not isinstance(data, dict):
            return {}
        raw = data.get("allocations")
        if not isinstance(raw, dict):
            return {}
        registry: dict[str, dict[str, int]] = {}
        for service, records in raw.items():
            if not isinstance(records, dict):
                continue
            entries: dict[str, int] = {}
            for name, port in records.items():
                if isinstance(port, bool) or not isinstance(port, int):
                    continue
                entries[str(name)] = port
            registry[str(service)] = entries
        return registry

    def _write_registry(self, registry: Mapping[str, Mapping[str, int]]) -> None:
        """Write the registry atomically, mode ``0600``."""
        payload = yaml.safe_dump({"allocations": registry}, sort_keys=True)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.chmod(_FILE_MODE)
        os.replace(tmp, self.state_path)


def _is_own(binding: ContainerPortBinding, own_containers: Sequence[str]) -> bool:
    """Whether ``binding`` belongs to the service that is asking for a port.

    A docker service owns its container by name; a compose service owns the
    containers compose labelled with its project name.
    """
    if not own_containers:
        return False
    if binding.name in own_containers:
        return True
    return bool(binding.compose_project) and binding.compose_project in own_containers


__all__ = ["DYNAMIC_PORT_RANGE", "NodePortAllocator"]
