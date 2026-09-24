"""Hook-level tests for the ``materialize_orchestrator_config`` handler.

These exercise the handler in isolation against bifrost.yaml loaded into
a tmp context. They verify the four invariants the orchestrator depends
on:

- Hook writes the pending config to the declared target.
- Hook is a no-op when no pending config is set (the "no body" case from
  the orchestrator's contract).
- YAML format round-trips.
- Unknown format fails loudly (ValueError) rather than writing the
  wrong shape.

The full start sequence (run hooks, run container) is exercised in
``test_docker_service_base`` and ``test_yaml_loader``; here we drive
the hook directly via :func:`genesis_worker.utils.services.hooks.run`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import load_service_spec
from genesis_worker.utils.services.hooks import PreStartHookContext
from genesis_worker.utils.services.hooks import run as run_hooks

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


def _load_bifrost(tmp_path: Path):
    """Load bifrost.yaml against a hermetic context rooted at ``tmp_path``.

    ``service_ctx`` builds a context where ``data_dir`` defaults to
    ``<tmp_path>/data``; the bifrost hook target resolves to
    ``<tmp_path>/data/data/config.json`` because bifrost's YAML
    declares ``data_dir_subpath: "data"`` and the hook target is
    ``$data_dir/data/config.json``.
    """
    spec_path = _DECLARATIVE_DIR / "bifrost.yaml"
    return load_service_spec(spec_path, ctx=service_ctx(tmp_path, name="bifrost"))


def _ctx(svc, tmp_path: Path) -> PreStartHookContext:
    """Build a hook context with the service's resolved state/data dirs."""
    return PreStartHookContext(
        service=svc,
        state_dir=svc._ctx.state_dir,
        data_dir=svc._ctx.data_dir,
    )


# --- happy path -----------------------------------------------------------


def test_hook_writes_orchestrator_config_to_target(tmp_path: Path) -> None:
    """The pending config lands on disk at the resolved target path."""
    svc = _load_bifrost(tmp_path)
    pending = {"providers": {"openai": {"keys": [{"name": "k", "value": "v"}]}}}
    svc._pending_orchestrator_config = pending

    run_hooks(list(svc.config.pre_start_hooks), _ctx(svc, tmp_path))

    target = tmp_path / "data" / "data" / "config.json"
    assert target.is_file()
    on_disk = json.loads(target.read_text())
    assert on_disk == pending


def test_hook_no_op_when_pending_config_is_none(tmp_path: Path) -> None:
    """Start was called without an orchestrator config — service uses on-disk state.

    Mirrors the orchestrator contract: when ``config`` is omitted from
    the request body, the existing on-disk configuration must be
    preserved. The hook is a no-op; no file is written.
    """
    svc = _load_bifrost(tmp_path)
    assert svc._pending_orchestrator_config is None

    run_hooks(list(svc.config.pre_start_hooks), _ctx(svc, tmp_path))

    target = tmp_path / "data" / "data" / "config.json"
    assert not target.exists()


def test_hook_overwrites_existing_target(tmp_path: Path) -> None:
    """A second start with a fresh config replaces the prior file.

    Today's behaviour for any new orchestrator call is "the worker
    accepts the new body and adopts it." The hook writes atomically;
    the prior contents are gone after the call.
    """
    svc = _load_bifrost(tmp_path)
    target = tmp_path / "data" / "data" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"old": True}))

    new_pending = {"providers": {"yoga": {"keys": []}}}
    svc._pending_orchestrator_config = new_pending
    run_hooks(list(svc.config.pre_start_hooks), _ctx(svc, tmp_path))

    assert json.loads(target.read_text()) == new_pending


# --- yaml format ----------------------------------------------------------


def test_hook_yaml_format(tmp_path: Path) -> None:
    """A spec declaring ``format: yaml`` round-trips through the hook."""
    payload = {
        "version": 1,
        "kind": "docker",
        "name": "yaml_demo",
        "display_name": "YAML Demo",
        "description": "yaml format demo",
        "category": "crawler",
        "capabilities": {
            "can_generate_config": False,
            "can_export_for_agent": False,
            "can_serve_llm": False,
            "can_serve_image": False,
            "can_train_models": False,
            "has_web_ui": True,
            "can_install": True,
        },
        "resource_estimate": {
            "vram_bytes_typical": 0,
            "vram_bytes_min": 0,
            "cpu_cores_recommended": 1,
        },
        "image": {"repo": "example/yamldemo", "tag": "v1"},
        "container": {
            "name": "yamldemo",
            "listen_port": 8080,
            "health_probe_path": "/health",
        },
        "pre_start_hooks": [
            {
                "kind": "materialize_orchestrator_config",
                "target": "$data_dir/data/config.yaml",
                "format": "yaml",
            }
        ],
    }
    spec_path = tmp_path / "yaml_demo.yaml"
    spec_path.write_text(yaml.safe_dump(payload))
    svc = load_service_spec(spec_path, ctx=service_ctx(tmp_path, name="yaml_demo"))

    pending = {"providers": {"openai": {"keys": [{"name": "k"}]}}}
    svc._pending_orchestrator_config = pending
    run_hooks(list(svc.config.pre_start_hooks), _ctx(svc, tmp_path))

    target = tmp_path / "data" / "data" / "config.yaml"
    on_disk = yaml.safe_load(target.read_text())
    assert on_disk == pending


# --- error paths ----------------------------------------------------------


def test_hook_unknown_format_raises(tmp_path: Path) -> None:
    """A typo (``format: toml``) fails loudly rather than writing the wrong shape."""
    svc = _load_bifrost(tmp_path)
    # Mutate the resolved hook entry to inject an unsupported format;
    # the loader doesn't validate ``format`` (it stays a hook-handler
    # concern) so we simulate a typo via direct mutation.
    hooks = list(svc.config.pre_start_hooks)
    hooks[0] = {**hooks[0], "format": "toml"}
    svc._pending_orchestrator_config = {"x": 1}

    with pytest.raises(ValueError, match="unsupported format"):
        run_hooks(hooks, _ctx(svc, tmp_path))


def test_hook_handles_pending_config_being_set_to_empty_dict(tmp_path: Path) -> None:
    """An empty config is a valid body — the hook writes it through verbatim.

    ``{}`` round-trips as ``{}`` on disk; the container boots with an
    empty config and bifrost upstream decides what that means. We
    don't reject empty payloads at the hook layer.
    """
    svc = _load_bifrost(tmp_path)
    svc._pending_orchestrator_config = {}
    run_hooks(list(svc.config.pre_start_hooks), _ctx(svc, tmp_path))

    target = tmp_path / "data" / "data" / "config.json"
    assert json.loads(target.read_text()) == {}
