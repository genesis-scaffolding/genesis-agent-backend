"""Host probes the port allocator needs to decide whether a port is free.

Three probes, all injected into the allocator so the tests stay hermetic:

- :func:`framework_ports` — the framework's own ports (worker API, worker UI)
  plus the env overrides that move them.
- :func:`container_port_bindings` — every container's *declared* host-port
  bindings, running or stopped.
- :func:`bindable` — the live bind test: can a socket take this port right
  now?

"Free" means free in the real world: the framework's own ports, other
services' fixed ports, container declarations, and anything already bound on
the host all count as taken (ADR-043 §5).
"""

from __future__ import annotations

import os
import socket

from ..process.docker import ContainerPortBinding, container_port_bindings

# The framework's own ports. The worker API and the Streamlit UI both bind
# on the node, so the allocator must never hand them out.
FRAMEWORK_API_PORT = 20985
FRAMEWORK_UI_PORT = 8501


def framework_ports() -> set[int]:
    """The framework's own ports, including the env overrides that move them.

    ``GENESIS_API_PORT`` / ``GENESIS_UI_PORT`` are read by the CLI entry
    points, so a node that moves them must move them here too.
    """
    ports = {FRAMEWORK_API_PORT, FRAMEWORK_UI_PORT}
    for env_name, port in (
        ("GENESIS_API_PORT", FRAMEWORK_API_PORT),
        ("GENESIS_UI_PORT", FRAMEWORK_UI_PORT),
    ):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        try:
            ports.add(int(raw))
        except ValueError:
            continue
        ports.discard(port)
    return ports


def bindable(port: int, host: str = "0.0.0.0") -> bool:
    """True iff a throwaway socket can bind ``port`` on ``host`` right now.

    ``SO_REUSEADDR`` is set to mirror ``docker-proxy`` — a port docker
    publishes stays bindable in this mode, which is why the allocator
    treats "our own container already publishes it" as free (see
    :func:`genesis_worker.utils.ports.allocator.NodePortAllocator`).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


__all__ = [
    "FRAMEWORK_API_PORT",
    "FRAMEWORK_UI_PORT",
    "ContainerPortBinding",
    "bindable",
    "container_port_bindings",
    "framework_ports",
]
