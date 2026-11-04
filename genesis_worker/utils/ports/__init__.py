"""Dynamic host-port allocation for declarative services (ADR-043).

A declarative service asks the framework for a host port by writing
``$auto_port.<name>`` in a ``port`` option. The marker rides the existing
``$`` substitution rounds untouched; the framework allocates a port from a
node-level range when the service starts, persists the allocation, and
materialises the integer into the runtime command.

- :mod:`.markers` — the grammar, in one place.
- :mod:`.allocator` — the node-level registry, its lock, and the free check.
- :mod:`.environment` — the host probes the allocator needs.
- :mod:`.binder` — the per-service view of the allocator.

The package is a leaf under ``genesis_worker.utils`` — plugins may import it
without violating the framework boundary (ADR-009).
"""

from .allocator import DYNAMIC_PORT_RANGE, NodePortAllocator
from .binder import PortBinder
from .environment import (
    FRAMEWORK_API_PORT,
    FRAMEWORK_UI_PORT,
    ContainerPortBinding,
    bindable,
    container_port_bindings,
    framework_ports,
)
from .markers import (
    AUTO_PORT_KEY_PREFIX,
    AUTO_PORT_MARKER_PATTERN,
    assert_no_markers,
    is_marker,
    marker_name,
)

__all__ = [
    "AUTO_PORT_KEY_PREFIX",
    "AUTO_PORT_MARKER_PATTERN",
    "DYNAMIC_PORT_RANGE",
    "FRAMEWORK_API_PORT",
    "FRAMEWORK_UI_PORT",
    "ContainerPortBinding",
    "NodePortAllocator",
    "PortBinder",
    "assert_no_markers",
    "bindable",
    "container_port_bindings",
    "framework_ports",
    "is_marker",
    "marker_name",
]
