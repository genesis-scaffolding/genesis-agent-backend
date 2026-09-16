"""Tests for the pre-start hook registry in ``utils/services/hooks.py``."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

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
    run(
        [
            {
                "kind": "ensure_persistent_token",
                "target": "$state_dir/token",
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
                "target": "$state_dir/token",
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
                    "target": "$state_dir/token",
                    "generator": "no_such_generator",
                }
            ],
            ctx,
        )


def test_state_dir_and_data_dir_placeholders_resolve(tmp_path: Path) -> None:
    """``$state_dir`` and ``$data_dir`` placeholders resolve to the context's paths."""
    state_dir = tmp_path / "state"
    data_dir = tmp_path / "data"
    ctx = PreStartHookContext(service=None, state_dir=state_dir, data_dir=data_dir)
    target_path = data_dir / "token"
    run(
        [
            {
                "kind": "ensure_persistent_token",
                "target": "$data_dir/token",
            }
        ],
        ctx,
    )
    assert target_path.is_file()
