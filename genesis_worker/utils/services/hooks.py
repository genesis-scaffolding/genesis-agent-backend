"""Pre-start hook registry — named handlers invoked by ``DockerService.start()``.

Hook entries arrive already-resolved at this point: the loader
substituted ``$state_dir`` / ``$data_dir`` etc. at construction time,
so handlers treat ``entry["target"]`` as an absolute path string and
don't need to know about placeholders. ``ctx.state_dir`` /
``ctx.data_dir`` are still carried for hooks that want to derive
paths dynamically.

Three handlers ship today:

- ``seed_yaml_whitelist`` -- lifted from ``services/sillytavern/config.py``;
  idempotently seeds a YAML whitelist key (loopback + docker bridge gateway
  + host LAN subnets + Tailscale CGNAT + user entries).
- ``ensure_persistent_token`` -- read-or-create a token file using
  :func:`genesis_worker.utils.services.ensure_persistent_file.ensure_persistent_file`.
- ``materialize_orchestrator_config`` (ADR-038) -- write the
  orchestrator-supplied config blob (``ctx.service._pending_orchestrator_config``)
  to ``entry["target"]`` before the container starts. No-op when no
  config was passed. Atomic write; JSON by default, YAML supported.

Adding a new hook kind is one function in this module + a registered name.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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


@register("materialize_orchestrator_config")
def _materialize_orchestrator_config(entry: dict, ctx: PreStartHookContext) -> None:
    """Write the orchestrator-supplied config to ``entry["target"]`` before start.

    ADR-038. The facade sets ``ctx.service._pending_orchestrator_config``
    before invoking ``start()``; this hook consumes it. No-op when the
    attribute is ``None`` — start was called without an orchestrator
    config, so the service uses its on-disk config (today's behaviour).

    Atomic write via tmp file + ``os.replace``, mirroring
    ``seed_yaml_whitelist``. Stale tmp artifacts from a prior crashed
    write are dropped so the ``os.replace`` doesn't trip on a
    half-written file.

    ``format`` defaults to ``json``; ``yaml`` is also supported. Unknown
    formats raise ``ValueError`` so a typo in the YAML fails loudly at
    start time rather than silently writing the wrong shape.
    """
    config = getattr(ctx.service, "_pending_orchestrator_config", None)
    if config is None:
        return

    target = Path(entry["target"])
    fmt = entry.get("format", "json")
    if fmt not in ("json", "yaml"):
        raise ValueError(
            f"materialize_orchestrator_config: unsupported format {fmt!r}; "
            "expected 'json' or 'yaml'"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    for stale in target.parent.glob(f"{target.name}.tmp.*"):
        try:
            stale.unlink()
        except OSError:
            pass

    tmp = target.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    if fmt == "json":
        with tmp.open("w") as f:
            json.dump(config, f, indent=2, sort_keys=False)
    else:
        with tmp.open("w") as f:
            yaml.safe_dump(config, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, target)


__all__ = [
    "HookHandler",
    "PreStartHookContext",
    "register",
    "registered_kinds",
    "run",
]
