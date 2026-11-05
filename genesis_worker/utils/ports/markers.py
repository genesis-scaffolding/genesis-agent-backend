"""The ``$auto_port.<name>`` marker grammar — one place, so every caller agrees.

A declarative service asks the framework for a host port by writing the marker
in a ``port`` option (or in a port-bearing spec field). The marker rides the
existing ``$`` substitution rounds untouched and is materialised into an
allocated integer when the kind builds its runtime command (ADR-043).

The grammar is deliberately tiny::

    $auto_port.<name>          # <name> matches [a-zA-Z0-9_-]+

``<name>`` is the identity of the port: one name, one port, within a service.
"""

from __future__ import annotations

import re
from typing import Any

AUTO_PORT_KEY_PREFIX = "auto_port."
AUTO_PORT_MARKER_PATTERN = r"^\$auto_port\.[a-zA-Z0-9_-]+$"

# The bare text a marker carries. ``assert_no_markers`` scans for this, not
# for the full pattern, because a marker embedded in a larger string (a
# compose template's ``"0.0.0.0:$auto_port.listen:3000"``) is still a marker.
AUTO_PORT_MARKER_TEXT = "$" + AUTO_PORT_KEY_PREFIX

_MARKER_RE = re.compile(AUTO_PORT_MARKER_PATTERN)


def is_marker(value: Any) -> bool:
    """True iff ``value`` is a well-formed ``$auto_port.<name>`` marker."""
    return isinstance(value, str) and _MARKER_RE.match(value) is not None


def marker_name(value: Any) -> str | None:
    """The ``<name>`` of a marker, or ``None`` when ``value`` is not one."""
    if not is_marker(value):
        return None
    assert isinstance(value, str)
    return value[len("$" + AUTO_PORT_KEY_PREFIX) :]


def assert_no_markers(payload: Any, where: str) -> None:
    """Raise when ``payload`` still carries a ``$auto_port.`` marker.

    The guard runs on every materialised runtime payload — the docker port
    map, the rendered compose file, the uv command line. A marker that
    survived means the framework failed to materialise a port it was asked
    to allocate, and the runtime command would carry literal ``$auto_port.x``
    text. That is a spec error, not a silent broken command.

    Only the ``$auto_port.`` prefix is scanned; an unrelated ``$SOMETHING`` in
    an env value is not a false positive.
    """
    found = _find_marker(payload)
    if found is not None:
        raise ValueError(
            f"{where}: unresolved auto-port marker {found!r} — the port was not materialised"
        )


def _find_marker(payload: Any) -> str | None:
    """The first ``$auto_port.`` marker inside ``payload``, or ``None``."""
    if isinstance(payload, str):
        return payload if AUTO_PORT_MARKER_TEXT in payload else None
    if isinstance(payload, dict):
        for key, value in payload.items():
            hit = _find_marker(key) or _find_marker(value)
            if hit is not None:
                return hit
        return None
    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            hit = _find_marker(item)
            if hit is not None:
                return hit
        return None
    return None


__all__ = [
    "AUTO_PORT_KEY_PREFIX",
    "AUTO_PORT_MARKER_PATTERN",
    "AUTO_PORT_MARKER_TEXT",
    "assert_no_markers",
    "is_marker",
    "marker_name",
]
