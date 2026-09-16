"""Pre-start hook registry — named handlers invoked by ``DockerService.start()``.

Hook entries arrive already-resolved at this point: the loader
substituted ``$state_dir`` / ``$data_dir`` etc. at construction time,
so handlers treat ``entry["target"]`` as an absolute path string and
don't need to know about placeholders. ``ctx.state_dir`` /
``ctx.data_dir`` are still carried for hooks that want to derive
paths dynamically.

Two handlers ship in v1:

- ``seed_yaml_whitelist`` -- lifted from ``services/sillytavern/config.py``;
  idempotently seeds a YAML whitelist key (loopback + docker bridge gateway
  + host LAN subnets + Tailscale CGNAT + user entries).
- ``ensure_persistent_token`` -- read-or-create a token file using
  :func:`genesis_worker.utils.services.ensure_persistent_file.ensure_persistent_file`.

Adding a new hook kind is one function in this module + a registered name.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ensure_persistent_file import (
    ensure_persistent_file,
    random_hex_32,
    random_urlsafe_32,
)
from .seed_yaml_whitelist import seed_yaml_whitelist

_GENERATORS: dict[str, Callable[[], str]] = {
    "random_hex_32": random_hex_32,
    "random_urlsafe_32": random_urlsafe_32,
}


@dataclass(frozen=True)
class PreStartHookContext:
    """The framework's view of the service during a hook run."""

    service: Any  # InferenceService (kept loose to avoid import cycles)
    state_dir: Path
    data_dir: Path


HookHandler = Callable[[dict, PreStartHookContext], None]

_REGISTRY: dict[str, HookHandler] = {}


def register(kind: str) -> Callable[[HookHandler], HookHandler]:
    """Decorator: register ``fn`` under ``kind`` for ``run()`` to dispatch to."""

    def decorator(fn: HookHandler) -> HookHandler:
        if kind in _REGISTRY:
            raise ValueError(f"hook kind {kind!r} already registered")
        _REGISTRY[kind] = fn
        return fn

    return decorator


def registered_kinds() -> list[str]:
    """Names of all registered hook kinds. Test/debug aid."""
    return sorted(_REGISTRY)


def run(hooks: list[dict], ctx: PreStartHookContext) -> None:
    """Dispatch every entry in ``hooks`` to its registered handler, in order.

    Each entry must carry a ``kind`` field naming a registered handler.
    Handlers are responsible for parsing their own per-hook fields from
    the dict -- the registry just dispatches.
    """
    for entry in hooks:
        kind = entry.get("kind")
        if not isinstance(kind, str):
            raise TypeError(f"hook entry missing 'kind': {entry!r}")
        handler = _REGISTRY.get(kind)
        if handler is None:
            raise ValueError(
                f"unknown pre-start hook kind {kind!r}; registered: {sorted(_REGISTRY)}"
            )
        handler(entry, ctx)


# --- handlers ---------------------------------------------------------------


def _resolve_mode(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("0o"):
            return int(text, 8)
        return int(text, 8)
    return 0o600


@register("seed_yaml_whitelist")
def _seed_yaml_whitelist(entry: dict, ctx: PreStartHookContext) -> None:
    target = Path(entry["target"])
    key = entry.get("key", "whitelist")
    extras = entry.get("extras") or []
    disable = entry.get("disable_docker_hosts", True)
    if not isinstance(extras, list):
        raise TypeError(f"seed_yaml_whitelist 'extras' must be a list, got {extras!r}")
    seed_yaml_whitelist(target, key=key, extras=list(extras), disable_docker_hosts=bool(disable))


@register("ensure_persistent_token")
def _ensure_persistent_token(entry: dict, ctx: PreStartHookContext) -> None:
    target = Path(entry["target"])
    mode = _resolve_mode(entry.get("mode", 0o600))
    generator_name = entry.get("generator", "random_hex_32")
    generator = _GENERATORS.get(generator_name)
    if generator is None:
        raise ValueError(
            f"unknown token generator {generator_name!r}; registered: {sorted(_GENERATORS)}"
        )
    ensure_persistent_file(target, mode=mode, generator=generator)


__all__ = [
    "HookHandler",
    "PreStartHookContext",
    "register",
    "registered_kinds",
    "run",
]
