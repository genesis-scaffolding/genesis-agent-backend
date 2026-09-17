"""YAML service loader -- parse + validate + construct one declarative spec.

The loader takes a path to a ``.yaml`` file and a context factory, and
returns a fully-constructed :class:`InferenceService`. Steps:

1. ``yaml.safe_load`` the file.
2. Validate against the discriminated union on ``kind``.
3. Build the runtime options pydantic model from the YAML's ``options:`` block.
4. Resolve placeholder strings (``$state_dir``, ``$data_dir``, ``$vault_path``,
   ``$options.X``, ``$auth.token``) against the resolved :class:`ServiceContext`.
5. Construct the matching ``DockerServiceConfig`` or ``UvServiceConfig``.
6. Instantiate ``DockerService`` or ``UvService``.

The placeholder helper is shared by every field that might reference a
context path -- env values, volume targets, hook targets, auth paths,
option defaults. It walks dicts and lists recursively so ``volumes``
with mixed placeholders works without callers caring.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ...contracts import (
    ServiceCapabilities,
    ServiceCategory,
    ServiceContext,
    ServiceResourceEstimate,
)
from . import spec as _spec_module
from .base import DeclarativeServiceBase
from .docker_service import AuthConfig, DockerService, DockerServiceConfig
from .spec import (
    DockerServiceSpec,
    UvServiceSpec,
    spec_to_options_model,
)
from .uv_service import UvService, UvServiceConfig

_PLACEHOLDER_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)")


def _resolve_string(
    value: str, ctx: ServiceContext, options: Mapping[str, Any], auth_token: Any
) -> Any:
    """Resolve ``$placeholders`` in ``value``.

    If the string is exactly one ``$placeholder``, return the typed
    value (bool, int, str, Path, None) — preserving the option's
    original type for fields like ``auth.enabled_option``. Mixed
    strings (e.g. ``"$state_dir/$options.subpath"``) fall through to
    regex substitution and always return a string.
    """
    match = _PLACEHOLDER_RE.fullmatch(value)
    if match:
        return _resolve_placeholder(match.group(1), ctx, options, auth_token)

    def _replace(match: re.Match[str]) -> str:
        return str(_resolve_placeholder(match.group(1), ctx, options, auth_token))

    return _PLACEHOLDER_RE.sub(_replace, value)


def _resolve_placeholder(
    key: str, ctx: ServiceContext, options: Mapping[str, Any], auth_token: Any
) -> Any:
    if key == "state_dir":
        return ctx.state_dir
    if key == "data_dir":
        return ctx.data_dir
    if key == "vault_path":
        return ctx.vault_path
    if key == "log_dir":
        return ctx.log_dir
    if key == "cache_dir":
        return ctx.cache_dir
    if key == "config_dir":
        return ctx.config_dir
    if key.startswith("options."):
        return options.get(key.split(".", 1)[1])
    # Unknown placeholder -- leave the marker intact so callers can spot it.
    return f"${key}"


def resolve_placeholders(
    value: Any,
    ctx: ServiceContext,
    options: Mapping[str, Any],
    auth_token: Any = None,
) -> Any:
    """Recursively replace ``$state_dir`` / ``$options.X`` markers in ``value``.

    Strings get the substitution (typed when the string is a single
    placeholder, string when mixed); dicts and lists are walked;
    everything else is returned untouched.
    """
    if isinstance(value, str):
        return _resolve_string(value, ctx, options, auth_token)
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, ctx, options, auth_token) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, ctx, options, auth_token) for v in value]
    return value


# --- the loader --------------------------------------------------------------


def _validate_spec(raw: dict) -> DockerServiceSpec | UvServiceSpec:
    """Dispatch a raw YAML dict to the right spec model by ``kind``.

    ``ServiceSpecUnion`` is the discriminated union type, but pydantic
    doesn't expose ``model_validate`` on ``Annotated`` instances directly.
    Routing through the kind key here keeps the dispatch type-safe.
    """
    kind = raw.get("kind")
    if kind == "docker":
        return DockerServiceSpec.model_validate(raw)
    if kind == "uv":
        return UvServiceSpec.model_validate(raw)
    raise ValueError(f"unknown service kind {kind!r}; expected 'docker' or 'uv'")


def _build_docker_config(
    resolved: dict,
    options_model: type[BaseModel],
) -> DockerServiceConfig:
    """Translate an already-resolved docker spec dict into ``DockerServiceConfig``.

    The caller (``load_service_spec``) has substituted every ``$placeholder``
    in the spec against ``ctx`` + the merged options dict. This
    function does no resolution -- it just maps resolved data into the
    config dataclass.
    """
    auth_cfg: AuthConfig | None = None
    if resolved.get("auth") is not None:
        auth = resolved["auth"]
        auth_cfg = AuthConfig(
            enabled=bool(auth["enabled_option"])
            if auth.get("enabled_option") is not None
            else None,
            fallback=auth.get("fallback_option") or None,
            token_env_var=auth["token_env_var"],
            token_file=Path(auth["token_file"]),
            token_file_mode=_spec_module._parse_mode(auth["token_file_mode"]),
            token_generator=auth["token_generator"],
        )

    container = resolved["container"]
    image = resolved["image"]
    ui = resolved["ui"]

    # ``model_dump`` flattens the category enum to its string value
    # and the capabilities / resource-estimate dataclasses to plain
    # dicts. Reconstruct them so the dataclass carries the right types.
    category = ServiceCategory(resolved["category"])
    capabilities = ServiceCapabilities(**resolved["capabilities"])
    resource_estimate = ServiceResourceEstimate(**resolved["resource_estimate"])

    return DockerServiceConfig(
        name=resolved["name"],
        display_name=resolved["display_name"],
        description=resolved["description"],
        category=category,
        capabilities=capabilities,
        resource_estimate=resource_estimate,
        options_model=options_model,
        log_filename=resolved["log_filename"],
        ui_pages=tuple(ui["status_panels"]),
        # docker-specific
        image_repo=image["repo"],
        image_tag=image["tag"],
        image_install_name=image.get("install_name") or resolved["name"],
        source_url=image.get("source_url"),
        container_name=container["name"],
        listen_host=container["listen_host"],
        listen_port=container["listen_port"],
        internal_port=container.get("internal_port"),
        public_host=container.get("public_host"),
        web_ui_path=container["web_ui_path"],
        restart_policy=container["restart_policy"],
        shm_size=container.get("shm_size"),
        health_probe_path=container["health_probe_path"],
        puid_default=container["puid_default"],
        pgid_default=container["pgid_default"],
        runtime=container.get("runtime"),
        gpu_flags=container.get("gpu_flags"),
        security_opts=container.get("security_opts"),
        extra_env=dict(resolved.get("env") or {}),
        extra_volumes=dict(resolved.get("volumes") or {}),
        extra_args=[],
        data_dir_subpath=resolved["data_dir_subpath"],
        pre_start_hooks=tuple(resolved.get("pre_start_hooks") or ()),
        auth=auth_cfg,
    )


def _build_uv_config(
    resolved: dict,
    options_model: type[BaseModel],
) -> UvServiceConfig:
    """Translate an already-resolved uv spec dict into ``UvServiceConfig``.

    The caller has substituted every ``$placeholder``. This function
    just maps resolved data into the config dataclass.
    """
    ui = resolved["ui"]
    category = ServiceCategory(resolved["category"])
    capabilities = ServiceCapabilities(**resolved["capabilities"])
    resource_estimate = ServiceResourceEstimate(**resolved["resource_estimate"])
    return UvServiceConfig(
        name=resolved["name"],
        display_name=resolved["display_name"],
        description=resolved["description"],
        category=category,
        capabilities=capabilities,
        resource_estimate=resource_estimate,
        options_model=options_model,
        log_filename=resolved["log_filename"],
        ui_pages=tuple(ui["status_panels"]),
        # uv-specific
        package_name=resolved["package_name"],
        binary_name=resolved["binary_name"],
        command=list(resolved.get("command") or []),
        install_env=dict(resolved.get("install_env") or {}),
        command_env=dict(resolved.get("command_env") or {}),
        listen_host=resolved["listen_host"],
        listen_port=resolved["listen_port"],
        public_host=resolved.get("public_host"),
        health_probe_path=resolved["health_probe_path"],
        health_timeout_s=resolved["health_timeout_s"],
        session_name=resolved.get("session_name"),
        graceful_stop_timeout_s=resolved["graceful_stop_timeout_s"],
    )


def load_service_spec(
    path: Path,
    *,
    ctx: ServiceContext,
) -> DeclarativeServiceBase:
    """Parse ``path`` and return a fully-constructed declarative service.

    ``ctx`` is the per-service ``ServiceContext`` already scoped by the
    caller (the registry uses ``path.stem`` as the name; tests pass a
    hermetic context built from a tmp dir). The filename and the
    YAML's ``name:`` field must agree -- ``sillytavern.yaml`` must
    declare ``name: sillytavern``. This catches the "renamed one but
    not the other" mistake at load time.

    The loader runs ONE resolution pass over the validated spec: every
    ``$state_dir`` / ``$data_dir`` / ``$options.X`` marker is replaced
    with its concrete value (paths, typed option values) before any
    config builder runs. Builders take the resolved dict and produce
    dataclasses with no placeholder machinery of their own.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: top-level YAML must be a mapping, got {type(raw).__name__}")

    try:
        spec: DockerServiceSpec | UvServiceSpec = _validate_spec(raw)
    except ValidationError as exc:
        raise ValueError(f"{path}: YAML failed schema validation:\n{exc}") from exc

    if spec.name != path.stem:
        raise ValueError(
            f"{path}: filename stem {path.stem!r} does not match spec name {spec.name!r}; "
            f"rename the file or fix the YAML's name field"
        )

    options_model = spec_to_options_model(
        spec.options, model_name=f"{spec.name.title()}YAMLOptions"
    )

    # Probe the merged options (user overrides > YAML defaults) so
    # ``$options.X`` resolves to the runtime value, not just the default.
    probe = options_model(**ctx.options)
    resolved = resolve_placeholders(spec.model_dump(), ctx, probe.model_dump())

    if isinstance(spec, DockerServiceSpec):
        config = _build_docker_config(resolved, options_model)
        return DockerService(ctx, config=config)

    config = _build_uv_config(resolved, options_model)
    return UvService(ctx, config=config)


__all__ = ["load_service_spec", "resolve_placeholders"]
