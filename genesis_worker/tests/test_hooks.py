"""Tests for the pre-start hook registry in ``utils/services/hooks.py``."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import yaml

from genesis_worker.utils.services.hooks import (
    PreStartHookContext,
    register,
    registered_kinds,
    run,
)


def test_registered_kinds_lists_v1_handlers() -> None:
    """The two v1 hook kinds ship registered."""
    assert {"seed_yaml_whitelist", "ensure_persistent_token"}.issubset(set(registered_kinds()))


def test_register_duplicate_raises() -> None:
    """Re-registering a kind is a programming error, not silent overwrite."""
    with pytest.raises(ValueError, match="already registered"):
        register("seed_yaml_whitelist")(lambda entry, ctx: None)


def test_run_unknown_kind_raises() -> None:
    """Unknown kinds surface a clear error so a YAML typo can't start a service."""
    ctx = PreStartHookContext(service=None, state_dir=Path("/tmp"), data_dir=Path("/tmp"))
    with pytest.raises(ValueError, match="unknown pre-start hook kind"):
        run([{"kind": "definitely_not_a_real_kind"}], ctx)


def test_run_missing_kind_raises() -> None:
    ctx = PreStartHookContext(service=None, state_dir=Path("/tmp"), data_dir=Path("/tmp"))
    with pytest.raises(TypeError, match="missing 'kind'"):
        run([{"target": "/tmp"}], ctx)


def test_run_dispatches_in_declared_order(tmp_path: Path) -> None:
    """Hooks fire in the order they're declared — order matters when one depends on another."""
    calls: list[str] = []

    @register("__test_first")
    def _first(_entry, _ctx):
        calls.append("first")

    @register("__test_second")
    def _second(_entry, _ctx):
        calls.append("second")

    try:
        run(
            [{"kind": "__test_first"}, {"kind": "__test_second"}],
            PreStartHookContext(service=None, state_dir=tmp_path, data_dir=tmp_path),
        )
        assert calls == ["first", "second"]
    finally:
        # Unregister by clearing the registry through the side-effect channel.
        from genesis_worker.utils.services import hooks as _h

        _h._REGISTRY.pop("__test_first", None)
        _h._REGISTRY.pop("__test_second", None)


def test_seed_yaml_whitelist_hook_writes_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``seed_yaml_whitelist`` hook writes a config.yaml on first call."""
    monkeypatch.setattr(
        "genesis_worker.utils.services.seed_yaml_whitelist._bridge_gateways", lambda: ["172.17.0.1"]
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.seed_yaml_whitelist._host_connected_subnets", list
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.seed_yaml_whitelist._host_own_addresses", list
    )
    target = tmp_path / "state" / "config.yaml"
    ctx = PreStartHookContext(service=None, state_dir=target.parent, data_dir=tmp_path / "data")
    run([{"kind": "seed_yaml_whitelist", "target": str(target), "key": "whitelist"}], ctx)
    assert target.is_file()


def test_seed_yaml_whitelist_hook_keyword_only(tmp_path: Path) -> None:
    """``target`` is required; missing or empty string raises."""
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    with pytest.raises(KeyError):
        run([{"kind": "seed_yaml_whitelist"}], ctx)


def test_ensure_persistent_token_writes_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "state" / "token"
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    # Hook entries arrive pre-resolved; the loader substitutes
    # ``$state_dir`` etc. at construction time.
    run(
        [
            {
                "kind": "ensure_persistent_token",
                "target": str(target),
                "mode": "0o600",
                "generator": "random_hex_32",
            }
        ],
        ctx,
    )
    assert target.is_file()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert len(target.read_text().strip()) == 64


def test_ensure_persistent_token_returns_existing(tmp_path: Path) -> None:
    target = tmp_path / "state" / "token"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("preset-token")
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    run(
        [
            {
                "kind": "ensure_persistent_token",
                "target": str(target),
                "generator": "random_hex_32",
            }
        ],
        ctx,
    )
    assert target.read_text() == "preset-token"


def test_ensure_persistent_token_unknown_generator(tmp_path: Path) -> None:
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    with pytest.raises(ValueError, match="unknown token generator"):
        run(
            [
                {
                    "kind": "ensure_persistent_token",
                    "target": str(tmp_path / "state" / "token"),
                    "generator": "no_such_generator",
                }
            ],
            ctx,
        )


# --- ensure_writable_dir --------------------------------------------------


def test_ensure_writable_dir_creates_and_world_writes_when_chown_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unprivileged worker cannot chown to another uid — fall back to 0777.

    prometheus runs as ``nobody`` (65534) inside the container while the
    bind-mounted host dir belongs to the desktop user, so the dir must
    be world-writable for the container user to write the TSDB.
    """
    target = tmp_path / "data" / "tsdb"

    def _no_chown(*_args, **_kwargs):
        raise PermissionError("not permitted")

    monkeypatch.setattr("genesis_worker.utils.services.hooks.os.chown", _no_chown)
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    run(
        [{"kind": "ensure_writable_dir", "target": str(target), "uid": 65534, "gid": 65534}],
        ctx,
    )
    assert target.is_dir()
    assert stat.S_IMODE(target.stat().st_mode) == 0o777


def test_ensure_writable_dir_chowns_when_privileged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the chown succeeds the dir is 0755 owned by the container uid."""
    target = tmp_path / "data" / "tsdb"
    calls: list[tuple[int, int]] = []

    def _fake_chown(path: Path, uid: int, gid: int) -> None:
        calls.append((uid, gid))
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("genesis_worker.utils.services.hooks.os.chown", _fake_chown)
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    run(
        [
            {
                "kind": "ensure_writable_dir",
                "target": str(target),
                "uid": 65534,
                "gid": 65534,
            }
        ],
        ctx,
    )
    assert calls == [(65534, 65534)]
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_ensure_writable_dir_is_idempotent(tmp_path: Path) -> None:
    """Re-running the hook on an existing dir keeps it usable."""
    target = tmp_path / "data" / "tsdb"
    target.mkdir(parents=True)
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    entry = {"kind": "ensure_writable_dir", "target": str(target), "mode": "0o770"}
    run([entry], ctx)
    run([entry], ctx)
    assert target.is_dir()
    assert stat.S_IMODE(target.stat().st_mode) == 0o770


# --- materialize_orchestrator_config (keyed selector) ----------------------


class _PendingConfigService:
    """Stand-in for ``InferenceService``: carries the pending config body.

    The facade stashes the orchestrator's JSON body on the service as
    ``_pending_orchestrator_config`` before ``start()``; the hook reads
    it from there.
    """

    def __init__(self, config: object) -> None:
        self._pending_orchestrator_config = config


def test_materialize_orchestrator_config_without_key_writes_whole_body(
    tmp_path: Path,
) -> None:
    """No ``key`` -> the whole config dict is written (unchanged behaviour)."""
    target = tmp_path / "config" / "config.json"
    body = {"providers": {"openai": {"keys": []}}, "other": 1}
    ctx = PreStartHookContext(
        service=_PendingConfigService(body),
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
    )
    run(
        [{"kind": "materialize_orchestrator_config", "target": str(target)}],
        ctx,
    )
    assert json.loads(target.read_text()) == body


def test_materialize_orchestrator_config_keyed_entry_writes_only_that_key(
    tmp_path: Path,
) -> None:
    """``key`` set -> only ``config[key]`` lands in the target file.

    One orchestrator config dict is split across several files by
    declaring one keyed entry per file (prometheus: server config,
    file-SD targets, alert rules).
    """
    target = tmp_path / "config" / "prometheus.yml"
    body = {
        "prometheus": {"global": {"scrape_interval": "15s"}},
        "targets": [{"targets": ["host:9090"], "labels": {"job": "worker"}}],
        "rules": [],
    }
    ctx = PreStartHookContext(
        service=_PendingConfigService(body),
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
    )
    run(
        [
            {
                "kind": "materialize_orchestrator_config",
                "key": "prometheus",
                "target": str(target),
                "format": "yaml",
            }
        ],
        ctx,
    )
    assert yaml.safe_load(target.read_text()) == body["prometheus"]


def test_materialize_orchestrator_config_several_keyed_entries_share_one_body(
    tmp_path: Path,
) -> None:
    """Three keyed entries consume ONE body and write three files."""
    body = {
        "prometheus": {"global": {"scrape_interval": "15s"}},
        "targets": [{"targets": ["host:9090"]}],
        "rules": [{"name": "x"}],
    }
    ctx = PreStartHookContext(
        service=_PendingConfigService(body),
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
    )
    run(
        [
            {
                "kind": "materialize_orchestrator_config",
                "key": "prometheus",
                "target": str(tmp_path / "config" / "prometheus.yml"),
                "format": "yaml",
            },
            {
                "kind": "materialize_orchestrator_config",
                "key": "targets",
                "target": str(tmp_path / "config" / "file_sd" / "targets.json"),
                "format": "json",
            },
            {
                "kind": "materialize_orchestrator_config",
                "key": "rules",
                "target": str(tmp_path / "config" / "rules" / "fleet.yml"),
                "format": "yaml",
            },
        ],
        ctx,
    )
    assert (
        yaml.safe_load((tmp_path / "config" / "prometheus.yml").read_text()) == body["prometheus"]
    )
    assert (
        json.loads((tmp_path / "config" / "file_sd" / "targets.json").read_text())
        == body["targets"]
    )
    assert (
        yaml.safe_load((tmp_path / "config" / "rules" / "fleet.yml").read_text()) == body["rules"]
    )


def test_materialize_orchestrator_config_missing_key_raises(tmp_path: Path) -> None:
    """A keyed entry whose key is absent from the body fails the start loudly.

    The stale on-disk file must not be used by accident: the hook
    raises before writing anything, so the previous file survives
    untouched and the container never starts.
    """
    target = tmp_path / "config" / "rules" / "fleet.yml"
    target.parent.mkdir(parents=True)
    target.write_text("stale: true\n")

    ctx = PreStartHookContext(
        service=_PendingConfigService({"prometheus": {}, "targets": []}),
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
    )
    with pytest.raises(ValueError, match="missing from the orchestrator config body"):
        run(
            [
                {
                    "kind": "materialize_orchestrator_config",
                    "key": "rules",
                    "target": str(target),
                    "format": "yaml",
                }
            ],
            ctx,
        )
    # The stale file was not overwritten.
    assert target.read_text() == "stale: true\n"


def test_materialize_orchestrator_config_keyed_entry_is_noop_without_pending_config(
    tmp_path: Path,
) -> None:
    """No config body stashed -> no-op, even with a ``key`` set.

    The start was called without an orchestrator config, so the
    on-disk config is preserved and the hook does nothing.
    """
    target = tmp_path / "config" / "prometheus.yml"
    ctx = PreStartHookContext(
        service=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data"
    )
    run(
        [
            {
                "kind": "materialize_orchestrator_config",
                "key": "prometheus",
                "target": str(target),
                "format": "yaml",
            }
        ],
        ctx,
    )
    assert not target.exists()


def test_materialize_orchestrator_config_keyed_entry_requires_a_mapping_body(
    tmp_path: Path,
) -> None:
    """A keyed entry against a non-mapping body fails loudly."""
    ctx = PreStartHookContext(
        service=_PendingConfigService(["not", "a", "mapping"]),
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
    )
    with pytest.raises(ValueError, match="not a mapping"):
        run(
            [
                {
                    "kind": "materialize_orchestrator_config",
                    "key": "prometheus",
                    "target": str(tmp_path / "config" / "prometheus.yml"),
                    "format": "yaml",
                }
            ],
            ctx,
        )


def test_materialize_orchestrator_config_empty_key_rejects(tmp_path: Path) -> None:
    """An empty ``key`` is a spec typo, not "the whole body"."""
    ctx = PreStartHookContext(
        service=_PendingConfigService({"prometheus": {}}),
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
    )
    with pytest.raises(ValueError, match="non-empty string"):
        run(
            [
                {
                    "kind": "materialize_orchestrator_config",
                    "key": "",
                    "target": str(tmp_path / "config" / "prometheus.yml"),
                }
            ],
            ctx,
        )
