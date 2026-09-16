"""Tests for the YAML service loader (ADR-035 phase 2).

Covers: happy-path for both kinds, version+unknown-key rejection,
unknown option types, and placeholder resolution for every supported
pattern. The loader runs against synthetic YAML strings; no real
Docker / uv-tool action happens here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import (
    DockerService,
    UvService,
    load_service_spec,
    resolve_placeholders,
    spec_to_options_model,
)
from genesis_worker.utils.services.spec import (
    DockerServiceSpec,
    OptionSpec,
    UvServiceSpec,
)

DOCKER_BASE: dict[str, Any] = {
    "version": 1,
    "kind": "docker",
    "name": "demo",
    "display_name": "Demo",
    "description": "demo service",
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
        "cpu_cores_recommended": 2,
    },
    "image": {"repo": "example/demo", "tag": "v1"},
    "container": {"name": "demo", "listen_port": 8080, "health_probe_path": "/health"},
}

UV_BASE: dict[str, Any] = {
    "version": 1,
    "kind": "uv",
    "name": "uv_demo",
    "display_name": "UV Demo",
    "description": "uv service",
    "category": "utility",
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
    "package_name": "uvdemo",
    "binary_name": "uvdemo",
    "command": ["run", "--port", "9999"],
    "listen_port": 9999,
}


def _write_yaml(tmp_path: Path, name: str, payload: dict) -> Path:
    import yaml

    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload))
    return path


# --- happy paths -----------------------------------------------------------


def test_load_docker_service_happy_path(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "demo.yaml", DOCKER_BASE)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))
    assert isinstance(svc, DockerService)
    assert svc.name == "demo"
    assert svc.display_name == "Demo"
    assert svc.image_ref == "example/demo:v1"
    assert svc.container_name == "demo"
    assert svc.listen_address == "0.0.0.0:8080"


def test_load_uv_service_happy_path(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "uv_demo.yaml", UV_BASE)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))
    assert isinstance(svc, UvService)
    assert svc.name == "uv_demo"
    assert svc.config.package_name == "uvdemo"
    assert svc.config.binary_name == "uvdemo"
    assert svc.config.command == ["run", "--port", "9999"]


def test_load_service_propagates_options_defaults(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["options"] = {
        "extra_args": {"type": "list[string]", "default": ["--quiet", "--debug"]},
        "max_connections": {"type": "int", "default": 50},
    }
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))
    # ``svc.options`` is the synthetic pydantic model built from the YAML.
    assert svc.options.extra_args == ["--quiet", "--debug"]  # type: ignore[attr-defined]
    assert svc.options.max_connections == 50  # type: ignore[attr-defined]


# --- schema rejection ------------------------------------------------------


def test_load_rejects_version_2(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["version"] = 2
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises((ValueError, Exception), match="version|model"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["not_a_field"] = "x"
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises(ValueError, match="YAML failed schema validation"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_unknown_option_type(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["options"] = {"flag": {"type": "list[bool]", "default": []}}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises(ValueError, match="unknown option type"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_missing_kind(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload.pop("kind")
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises(ValueError, match="YAML failed schema validation|kind"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_top_level_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(TypeError, match="top-level YAML must be a mapping"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_unknown_kind(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["kind"] = "lambda"
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises(ValueError, match="unknown service kind"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


# --- placeholder resolution -----------------------------------------------


def test_resolve_state_dir_in_env_value(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["env"] = {"DATA_DIR": "$state_dir/data"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_env["DATA_DIR"] == str(ctx.state_dir / "data")


def test_resolve_data_dir_in_volume_target(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["volumes"] = {"/data": "$data_dir/files"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_volumes["/data"] == str(ctx.data_dir / "files")


def test_resolve_vault_path_placeholder(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["env"] = {"MODELS": "$vault_path"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    # Single-placeholder strings resolve to the typed value (Path here).
    assert svc.config.extra_env["MODELS"] == ctx.vault_path


def test_resolve_options_placeholder(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["options"] = {"label": {"type": "string", "default": "hello"}}
    payload["env"] = {"GREETING": "$options.label"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_env["GREETING"] == "hello"


def test_resolve_unresolved_marker_is_left_intact(tmp_path: Path) -> None:
    """Unknown placeholders survive as ``$key`` so the user can spot them."""
    payload = dict(DOCKER_BASE)
    payload["env"] = {"X": "$not_a_real_key"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert svc.config.extra_env["X"] == "$not_a_real_key"


def test_resolve_recurses_into_nested_dicts(tmp_path: Path) -> None:
    """Nested dicts and lists are walked; scalars leave untouched."""
    nested = {"a": {"b": "$state_dir/nested"}}
    payload = dict(DOCKER_BASE)
    payload["volumes"] = nested  # type: ignore[assignment]
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert "/nested" in svc.config.extra_volumes["a"]["b"]


def test_resolve_handles_list_values(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["env"] = {"PATH_PARTS": ["$state_dir/a", "$data_dir/b", "literal"]}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    parts = svc.config.extra_env["PATH_PARTS"]
    # Mixed strings go through regex substitution (string result).
    assert parts[0] == str(ctx.state_dir / "a")
    assert parts[1] == str(ctx.data_dir / "b")
    assert parts[2] == "literal"


# --- resolve_placeholders direct -----------------------------------------


def test_resolve_placeholders_direct_call(tmp_path: Path) -> None:
    ctx = service_ctx(tmp_path, name="svc")
    out = resolve_placeholders(
        {"k": "$state_dir/x", "list": ["$vault_path"], "raw": 42, "flag": True},
        ctx,
        {},
        None,
    )
    # Mixed strings produce strings (regex sub stringifies the
    # replacement). Single-placeholder strings keep the typed value.
    assert out["k"] == str(ctx.state_dir / "x")
    assert out["list"] == [ctx.vault_path]
    assert out["raw"] == 42
    assert out["flag"] is True


# --- spec_to_options_model ------------------------------------------------


def test_spec_to_options_model_builds_int_field() -> None:
    spec = OptionSpec(type="int", default=42)
    M = spec_to_options_model({"x": spec})
    inst = M()
    assert inst.x == 42  # type: ignore[attr-defined]


def test_spec_to_options_model_marks_optional_fields_noneable() -> None:
    spec = OptionSpec(type="string", optional=True, default=None)
    M = spec_to_options_model({"label": spec})
    inst = M()
    assert inst.label is None  # type: ignore[attr-defined]
    inst2 = M(label="set")  # type: ignore[call-arg]
    assert inst2.label == "set"  # type: ignore[attr-defined]


def test_spec_to_options_model_list_string_default_empty() -> None:
    spec = OptionSpec(type="list[string]", default=[])
    M = spec_to_options_model({"tags": spec})
    inst = M()
    assert inst.tags == []  # type: ignore[attr-defined]


def test_spec_to_options_model_list_int_with_values() -> None:
    spec = OptionSpec(type="list[int]", default=[1, 2, 3])
    M = spec_to_options_model({"xs": spec})
    inst = M()
    assert inst.xs == [1, 2, 3]  # type: ignore[attr-defined]


def test_spec_to_options_model_list_path_coerces_to_path() -> None:
    spec = OptionSpec(type="list[path]", default=["/tmp/a", "/tmp/b"])
    M = spec_to_options_model({"paths": spec})
    from pathlib import Path

    inst = M()
    assert inst.paths == [Path("/tmp/a"), Path("/tmp/b")]  # type: ignore[attr-defined]


def test_spec_to_options_model_unknown_type_raises() -> None:
    spec = OptionSpec(type="list[bool]", default=[])
    with pytest.raises(ValueError, match="unknown option type"):
        spec_to_options_model({"flags": spec})


# --- spec model direct ---------------------------------------------------


def test_docker_spec_rejects_unknown_top_level() -> None:
    bad = dict(DOCKER_BASE)
    bad["extra_field"] = "boom"
    with pytest.raises(Exception, match="extra_field|Extra"):
        DockerServiceSpec.model_validate(bad)


def test_uv_spec_rejects_unknown_top_level() -> None:
    bad = dict(UV_BASE)
    bad["extra_field"] = "boom"
    with pytest.raises(Exception, match="extra_field|Extra"):
        UvServiceSpec.model_validate(bad)


def test_docker_spec_rejects_unknown_option_type() -> None:
    bad = dict(DOCKER_BASE)
    bad["options"] = {"flag": {"type": "decimal", "default": 1.5}}
    with pytest.raises(Exception, match="decimal|extra"):
        DockerServiceSpec.model_validate(bad)


# --- auth block ---------------------------------------------------------


def test_docker_with_auth_block_resolves_token_file(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["auth"] = {
        "enabled_option": None,
        "token_env_var": "API_TOKEN",
        "token_file": "$state_dir/api_token",
        "token_file_mode": "0o600",
        "token_generator": "random_hex_32",
        "fallback_option": None,
    }
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.auth is not None
    assert svc.config.auth.token_file == ctx.state_dir / "api_token"
    assert svc.config.auth.token_file_mode == 0o600


def test_docker_with_pre_start_hook_resolves_state_dir(tmp_path: Path) -> None:
    payload = dict(DOCKER_BASE)
    payload["pre_start_hooks"] = [
        {
            "kind": "ensure_persistent_token",
            "target": "$state_dir/api_token",
            "mode": "0o640",
        }
    ]
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    entry = svc.config.pre_start_hooks[0]
    assert entry["kind"] == "ensure_persistent_token"
    # ``target`` is resolved at construction; the handler reads an
    # absolute path string.
    assert entry["target"] == str(ctx.state_dir / "api_token")
