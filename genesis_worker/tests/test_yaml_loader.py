"""Tests for the YAML service loader (ADR-035 phase 2).

Covers: happy-path for both kinds, version+unknown-key rejection,
unknown option types, and placeholder resolution for every supported
pattern. The loader runs against synthetic YAML strings; no real
Docker / uv-tool action happens here.
"""

from __future__ import annotations

import copy
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


def _copy(base: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a base payload so test mutations don't leak across cases.

    The fixtures below share nested dicts (capabilities, container,
    ...) across calls when only the top level is copied. Tests that
    overwrite ``container.listen_port`` mutate the shared dict, so
    later tests see the wrong value. A deep copy isolates each case.
    """
    return copy.deepcopy(base)


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
    payload = _copy(DOCKER_BASE)
    payload["options"] = {
        "extra_args": {"type": "list[string]", "default": ["--quiet", "--debug"]},
        "max_connections": {"type": "int", "default": 50},
    }
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))
    # ``svc.options`` is the synthetic pydantic model built from the YAML.
    assert svc.options.extra_args == ["--quiet", "--debug"]  # type: ignore[attr-defined]
    assert svc.options.max_connections == 50  # type: ignore[attr-defined]


# --- ADR-036: option defaults resolve $variable markers; map options merge -


def test_option_default_with_data_dir_resolves(tmp_path: Path) -> None:
    """An option default like ``$data_dir/..`` resolves to the host path.

    Without the second-pass resolution in the loader, ``svc.config.extra_volumes``
    would carry the literal string ``"$data_dir/.."`` and docker would
    reject it. The two-pass substitution produces the resolved string
    (the literal ``$data_dir/..`` becomes ``<data_dir>/..``).
    """
    payload = _copy(DOCKER_BASE)
    payload["options"] = {
        "pictures_dir": {"type": "path", "default": "$data_dir/.."},
    }
    payload["volumes"] = {"/data/originals": "$options.pictures_dir"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name=path.stem)
    svc = load_service_spec(path, ctx=ctx)
    expected = str(ctx.data_dir) + "/.."
    assert svc.config.extra_volumes["/data/originals"] == expected


def test_listen_port_substitutes_option(tmp_path: Path) -> None:
    """``container.listen_port: $options.web_port`` resolves at construction.

    Lifts the ADR-035 carve-out that excluded ``listen_port`` from
    ``$variable`` substitution (ADR-036).
    """
    payload = _copy(DOCKER_BASE)
    payload["container"]["listen_port"] = "$options.web_port"  # type: ignore[index]
    payload["options"] = {"web_port": {"type": "port", "default": 9090}}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name=path.stem)
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.listen_port == 9090
    # User override propagates too.
    ctx2 = service_ctx(tmp_path, name=path.stem, options={"web_port": 12345})
    svc2 = load_service_spec(path, ctx=ctx2)
    assert svc2.config.listen_port == 12345


def test_listen_port_substitutes_option_invalid_value_raises(tmp_path: Path) -> None:
    """An out-of-range port override fails at options validation, not docker run."""
    payload = _copy(DOCKER_BASE)
    payload["container"]["listen_port"] = "$options.web_port"  # type: ignore[index]
    payload["options"] = {"web_port": {"type": "port", "default": 9090}}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name=path.stem, options={"web_port": 99999})
    with pytest.raises(Exception):
        load_service_spec(path, ctx=ctx)


def test_listen_host_substitutes_option(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["container"]["listen_host"] = "$options.bind_addr"  # type: ignore[index]
    payload["options"] = {"bind_addr": {"type": "string", "default": "127.0.0.1"}}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name=path.stem)
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.listen_host == "127.0.0.1"


def test_internal_port_substitutes_option(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["container"]["internal_port"] = "$options.app_port"  # type: ignore[index]
    payload["options"] = {"app_port": {"type": "port", "default": 8080}}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name=path.stem)
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.internal_port == 8080


def test_extra_env_merges_with_yaml_defaults(tmp_path: Path) -> None:
    """``extra_env`` option merges additively into ``config.extra_env``.

    YAML-declared typed knobs stay in place; the user's long-tail
    additions live alongside. A user addition colliding with a
    YAML-declared key wins (the user picked the long tail
    deliberately).
    """
    payload = _copy(DOCKER_BASE)
    payload["env"] = {"YAML_TYPED": "$options.typed_knob", "SHARED": "yaml-wins-not-here"}
    payload["options"] = {
        "typed_knob": {"type": "string", "default": "typed-default"},
        "extra_env": {"type": "env_map", "default": {}},
    }
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(
        tmp_path,
        name=path.stem,
        options={
            "extra_env": {"PHOTOPRISM_SITE_URL": "https://example.com", "SHARED": "user-wins"}
        },
    )
    svc = load_service_spec(path, ctx=ctx)
    env = svc.config.extra_env
    assert env["YAML_TYPED"] == "typed-default"
    assert env["PHOTOPRISM_SITE_URL"] == "https://example.com"
    # User addition wins on collision.
    assert env["SHARED"] == "user-wins"


def test_extra_mounts_merges_and_coerces_paths(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["volumes"] = {"/data/yaml": "$data_dir/yaml"}
    payload["options"] = {"extra_mounts": {"type": "mount_map", "default": {}}}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(
        tmp_path,
        name=path.stem,
        options={"extra_mounts": {"/data/extra": "/host/extra"}},
    )
    svc = load_service_spec(path, ctx=ctx)
    volumes = svc.config.extra_volumes
    assert volumes["/data/yaml"] == str(ctx.data_dir / "yaml")
    assert volumes["/data/extra"] == "/host/extra"


def test_option_specs_exposed_on_config(tmp_path: Path) -> None:
    """The loader hands the original OptionSpec dict to the config.

    The ``configure`` UI panel reads this to discover ``ui_label``,
    ``ui_help``, ``ui_group`` (ADR-036). Without this, the panel
    would have to re-parse the YAML.
    """
    payload = _copy(DOCKER_BASE)
    payload["options"] = {
        "web_port": {
            "type": "port",
            "default": 2342,
            "ui_label": "Web UI port",
            "ui_group": "Network",
        }
    }
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))
    assert "web_port" in svc.config.option_specs
    assert svc.config.option_specs["web_port"].ui_label == "Web UI port"
    assert svc.config.option_specs["web_port"].ui_group == "Network"


# --- schema rejection ------------------------------------------------------


def test_load_rejects_version_2(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["version"] = 2
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises((ValueError, Exception), match="version|model"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["not_a_field"] = "x"
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises(ValueError, match="YAML failed schema validation"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_unknown_option_type(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["options"] = {"flag": {"type": "list[bool]", "default": []}}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises(ValueError, match="unknown option type"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


def test_load_rejects_missing_kind(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
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
    payload = _copy(DOCKER_BASE)
    payload["kind"] = "lambda"
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    with pytest.raises(ValueError, match="unknown service kind"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))


# --- placeholder resolution -----------------------------------------------


def test_resolve_state_dir_in_env_value(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["env"] = {"DATA_DIR": "$state_dir/data"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_env["DATA_DIR"] == str(ctx.state_dir / "data")


def test_resolve_data_dir_in_volume_target(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["volumes"] = {"/data": "$data_dir/files"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_volumes["/data"] == str(ctx.data_dir / "files")


def test_resolve_vault_path_placeholder(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["env"] = {"MODELS": "$vault_path"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    # Single-placeholder strings coerce to str so they fit env dicts
    # (ADR-036); the resolved Path becomes its string form.
    assert svc.config.extra_env["MODELS"] == str(ctx.vault_path)


def test_resolve_media_vault_path_placeholder(tmp_path: Path) -> None:
    """``$media_vault_path`` resolves to ``ctx.media_vault_path`` (ADR-037).

    Parallel to ``$vault_path`` — single-placeholder strings return the
    Path's string form so they fit env dicts without further coercion.
    """
    payload = _copy(DOCKER_BASE)
    payload["env"] = {"MEDIA": "$media_vault_path"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_env["MEDIA"] == str(ctx.media_vault_path)


def test_resolve_media_vault_path_placeholder_mixed(tmp_path: Path) -> None:
    """Mixed strings (placeholder + literal) substitute via the regex path."""
    payload = _copy(DOCKER_BASE)
    payload["volumes"] = {"/photoprism/originals": "$media_vault_path/photoprism"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    expected = f"{ctx.media_vault_path}/photoprism"
    assert svc.config.extra_volumes["/photoprism/originals"] == expected


def test_resolve_media_vault_path_placeholder_recursive(tmp_path: Path) -> None:
    """Placeholder substitution recurses into list values (ADR-036)."""
    payload = _copy(DOCKER_BASE)
    payload["env"] = {"MEDIA_LIST": ["$media_vault_path"]}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_env["MEDIA_LIST"] == [str(ctx.media_vault_path)]


def test_resolve_options_placeholder(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
    payload["options"] = {"label": {"type": "string", "default": "hello"}}
    payload["env"] = {"GREETING": "$options.label"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    assert svc.config.extra_env["GREETING"] == "hello"


def test_resolve_unresolved_marker_is_left_intact(tmp_path: Path) -> None:
    """Unknown placeholders survive as ``$key`` so the user can spot them."""
    payload = _copy(DOCKER_BASE)
    payload["env"] = {"X": "$not_a_real_key"}
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert svc.config.extra_env["X"] == "$not_a_real_key"


def test_resolve_recurses_into_nested_dicts(tmp_path: Path) -> None:
    """Nested dicts and lists are walked; scalars leave untouched."""
    nested = {"a": {"b": "$state_dir/nested"}}
    payload = _copy(DOCKER_BASE)
    payload["volumes"] = nested  # type: ignore[assignment]
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert "/nested" in svc.config.extra_volumes["a"]["b"]


def test_resolve_handles_list_values(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
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
    # Strings get substituted (mixed strings -> regex sub; single
    # placeholder -> stringified typed value). Non-strings pass
    # through unchanged.
    assert out["k"] == str(ctx.state_dir / "x")
    assert out["list"] == [str(ctx.vault_path)]
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


def test_spec_to_options_model_port_validates_range() -> None:
    spec = OptionSpec(type="port", default=2342)
    M = spec_to_options_model({"web_port": spec})
    inst = M()
    assert inst.web_port == 2342  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="less than|Input should be"):
        M(web_port=99999)  # type: ignore[call-arg]
    with pytest.raises(Exception, match="greater than|Input should be"):
        M(web_port=0)  # type: ignore[call-arg]


def test_spec_to_options_model_port_optional_accepts_none() -> None:
    spec = OptionSpec(type="port", optional=True, default=None)
    M = spec_to_options_model({"web_port": spec})
    assert M().web_port is None  # type: ignore[attr-defined]
    assert M(web_port=8080).web_port == 8080  # type: ignore[call-arg]


def test_spec_to_options_model_env_map_default_empty() -> None:
    spec = OptionSpec(type="env_map", default={})
    M = spec_to_options_model({"extra_env": spec})
    assert M().extra_env == {}  # type: ignore[attr-defined]


def test_spec_to_options_model_env_map_accepts_user_dict() -> None:
    spec = OptionSpec(type="env_map", default={})
    M = spec_to_options_model({"extra_env": spec})
    inst = M(extra_env={"FOO": "bar", "BAZ": "qux"})  # type: ignore[call-arg]
    assert inst.extra_env == {"FOO": "bar", "BAZ": "qux"}  # type: ignore[attr-defined]


def test_spec_to_options_model_mount_map_coerces_paths() -> None:
    spec = OptionSpec(type="mount_map", default={})
    M = spec_to_options_model({"extra_mounts": spec})
    inst = M(extra_mounts={"/container/path": "/host/path"})  # type: ignore[call-arg]
    assert inst.extra_mounts == {"/container/path": Path("/host/path")}  # type: ignore[attr-defined]


def test_spec_to_options_model_ui_metadata_passes_through() -> None:
    spec = OptionSpec(
        type="string",
        default="",
        ui_label="Web UI port",
        ui_help="Host-side port the web UI listens on",
        ui_group="Network",
    )
    M = spec_to_options_model({"web_port": spec})
    field_info = M.model_fields["web_port"]
    assert field_info.default == ""
    # The ui_label / ui_help / ui_group fields live on the OptionSpec
    # itself; the synthesised model only carries the typed shape.
    assert spec.ui_label == "Web UI port"
    assert spec.ui_help == "Host-side port the web UI listens on"
    assert spec.ui_group == "Network"


def test_spec_to_options_model_rejects_unknown_ui_metadata_field() -> None:
    with pytest.raises(Exception, match="Extra|extra_field"):
        OptionSpec.model_validate({"type": "string", "ui_label": "x", "ui_unknown": "y"})


def test_option_spec_ui_metadata_defaults_to_empty() -> None:
    spec = OptionSpec(type="int", default=42)
    assert spec.ui_label == ""
    assert spec.ui_help == ""
    assert spec.ui_group == ""


# --- spec model direct ---------------------------------------------------


def test_docker_spec_rejects_unknown_top_level() -> None:
    bad = _copy(DOCKER_BASE)
    bad["extra_field"] = "boom"
    with pytest.raises(Exception, match="extra_field|Extra"):
        DockerServiceSpec.model_validate(bad)


def test_uv_spec_rejects_unknown_top_level() -> None:
    bad = _copy(UV_BASE)
    bad["extra_field"] = "boom"
    with pytest.raises(Exception, match="extra_field|Extra"):
        UvServiceSpec.model_validate(bad)


def test_docker_spec_rejects_unknown_option_type() -> None:
    bad = _copy(DOCKER_BASE)
    bad["options"] = {"flag": {"type": "decimal", "default": 1.5}}
    with pytest.raises(Exception, match="decimal|extra"):
        DockerServiceSpec.model_validate(bad)


# --- auth block ---------------------------------------------------------


def test_docker_with_auth_block_resolves_token_file(tmp_path: Path) -> None:
    payload = _copy(DOCKER_BASE)
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
    payload = _copy(DOCKER_BASE)
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


# --- materialize_orchestrator_config hook (ADR-038) ------------------------


def test_docker_with_materialize_orchestrator_config_hook_resolves_target(
    tmp_path: Path,
) -> None:
    """The loader substitutes ``$data_dir`` in the hook target at construction.

    The hook handler treats ``target`` as an absolute path string, so the
    substitution has to happen at YAML-load time (where it already does
    for the other pre-start hooks).
    """
    payload = _copy(DOCKER_BASE)
    payload["pre_start_hooks"] = [
        {
            "kind": "materialize_orchestrator_config",
            "target": "$data_dir/data/config.json",
        }
    ]
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    entry = svc.config.pre_start_hooks[0]
    assert entry["kind"] == "materialize_orchestrator_config"
    assert entry["target"] == str(ctx.data_dir / "data" / "config.json")


def test_docker_with_materialize_orchestrator_config_yaml_format(
    tmp_path: Path,
) -> None:
    """``format: yaml`` is preserved through the loader — the handler reads it."""
    payload = _copy(DOCKER_BASE)
    payload["pre_start_hooks"] = [
        {
            "kind": "materialize_orchestrator_config",
            "target": "$data_dir/config.yaml",
            "format": "yaml",
        }
    ]
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)
    entry = svc.config.pre_start_hooks[0]
    assert entry["format"] == "yaml"


def test_docker_with_materialize_orchestrator_config_unknown_format_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in ``format`` (e.g. ``toml``) fails loudly at start time, not silently.

    We monkeypatch the container's ``run`` so the start sequence proceeds
    all the way to the hook dispatch — we want the ValueError to fire
    from the hook, not the not-available guard earlier in ``start``.
    """
    import json as _json

    from genesis_worker.utils.services.docker_service import DockerContainer

    payload = _copy(DOCKER_BASE)
    payload["pre_start_hooks"] = [
        {
            "kind": "materialize_orchestrator_config",
            "target": "$data_dir/data/config.json",
            "format": "toml",
        }
    ]
    path = _write_yaml(tmp_path, "demo.yaml", payload)
    ctx = service_ctx(tmp_path, name="demo")
    svc = load_service_spec(path, ctx=ctx)

    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: True))
    monkeypatch.setattr(
        DockerContainer,
        "run",
        lambda *args, **kwargs: _json.dumps({"ok": True}),
    )
    svc._pending_orchestrator_config = {"x": 1}

    with pytest.raises(ValueError, match="unsupported format"):
        svc.start()
