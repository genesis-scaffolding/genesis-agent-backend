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
  An optional ``key`` selector writes ``config[key]`` instead of the
  whole body, so one config dict can be split across several files.
- ``ensure_writable_dir`` -- make a bind-mount target writable by a
  container user that is not the host user (e.g. prometheus, which runs
  as ``nobody`` / uid 65534).

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
    key = entry.get("key") or "whitelist"
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


@register("ensure_writable_dir")
def _ensure_writable_dir(entry: dict, ctx: PreStartHookContext) -> None:
    """Prepare a bind-mount target so a non-host-user container can write to it.

    Some images run as a fixed uid inside the container regardless of
    the host user — prometheus runs as ``nobody`` (65534). A bind mount
    keeps the HOST directory's ownership and mode, so that uid needs
    write access to the host path or the service dies at startup with
    "permission denied".

    The hook chowns the directory to ``uid``/``gid`` when the worker is
    privileged enough to do so (it runs as root). When it cannot — the
    common case, a worker running as the desktop user — it falls back
    to a world-writable mode, because an unprivileged owner cannot
    grant write access to a different uid any other way.

    Entry fields:

    - ``target`` -- host path (required).
    - ``uid`` / ``gid`` -- numeric ids to chown to (optional).
    - ``mode`` -- chmod mode used when the chown could not happen
      (default ``0o777``). After a successful chown the directory is
      chmod'ed ``0o755`` so the new owner keeps write access while
      others only read.
    """
    target = Path(entry["target"])
    target.mkdir(parents=True, exist_ok=True)

    uid = entry.get("uid")
    gid = entry.get("gid")
    chowned = False
    if isinstance(uid, int) and isinstance(gid, int):
        try:
            os.chown(target, uid, gid)
            chowned = True
        except OSError:
            # Not privileged enough (or the filesystem refuses). The
            # world-writable fallback below covers the common case.
            chowned = False

    mode = _resolve_mode(entry.get("mode", "0o777")) if not chowned else 0o755
    try:
        target.chmod(mode)
    except OSError:
        pass


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

    Entry fields:

    - ``target`` -- host path to write (required).
    - ``format`` -- ``json`` (default) or ``yaml``.
    - ``key`` -- optional selector. When set, the handler writes
      ``config[key]`` — one slice of the orchestrator's config body —
      instead of the whole body. Several keyed entries can therefore
      consume ONE config dict and split it across several files
      (prometheus: server config / file-SD targets / alert rules).

      When ``key`` is set but absent from the config body, the handler
      raises ``ValueError``: a stale on-disk file must never be used by
      accident because the new body happened to omit a key. The failure
      propagates out of ``start()``, so the container never starts.
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

    payload = config
    key = entry.get("key")
    if key is not None:
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"materialize_orchestrator_config: 'key' must be a non-empty string, got {key!r}"
            )
        if not isinstance(config, dict):
            raise ValueError(
                "materialize_orchestrator_config: 'key' is set but the orchestrator "
                f"config body is not a mapping (got {type(config).__name__})"
            )
        if key not in config:
            raise ValueError(
                f"materialize_orchestrator_config: key {key!r} is missing from the "
                f"orchestrator config body; available keys: {sorted(config)}"
            )
        payload = config[key]

    target.parent.mkdir(parents=True, exist_ok=True)
    for stale in target.parent.glob(f"{target.name}.tmp.*"):
        try:
            stale.unlink()
        except OSError:
            pass

    tmp = target.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    if fmt == "json":
        with tmp.open("w") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
    else:
        with tmp.open("w") as f:
            yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, target)


__all__ = [
    "HookHandler",
    "PreStartHookContext",
    "register",
    "registered_kinds",
    "run",
]
