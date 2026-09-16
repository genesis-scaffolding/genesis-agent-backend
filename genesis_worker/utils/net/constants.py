"""Network-related constants used across the worker.

Lifted from the per-service SillyTavern config-seeder so other code
(utilities, hooks, future services) can reference the same range
without re-declaring the magic string.
"""

TAILSCALE_CGNAT = "100.64.0.0/10"

__all__ = ["TAILSCALE_CGNAT"]
