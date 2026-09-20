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
    OptionSpec,
    UvServiceSpec,
    spec_to_options_model,
)
from .uv_service import UvService, UvServiceConfig

_PLACEHOLDER_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)")


def _resolve_string(
    value: str, ctx: ServiceContext, options: Mapping[str, Any], auth_token: Any
) -> Any:
    """Resolve ``$placeholders`` in ``value``.

    Single-placeholder strings return the typed value when the
    placeholder is ``$options.X`` (so ``$options.jwt_enabled`` rounds
    trips as ``bool`` for the auth block) or when the typed value is
    already a string. Otherwise the typed value is stringified --
    notably ``Path`` from ``$data_dir`` / ``$state_dir`` -- so the
    result fits ``env`` / ``volumes`` dicts that pydantic validates
    as ``dict[str, str]``.

    Mixed strings always return a string via regex substitution
    (stringified replacements). The env dict gets a post-pass that
    stringifies ``bool`` values into ``"true"`` / ``"false"`` so
    docker run gets the conventional lowercase form (ADR-036).
    """
    match = _PLACEHOLDER_RE.fullmatch(value)
    if match:
        resolved = _resolve_placeholder(match.group(1), ctx, options, auth_token)
        if resolved is None:
            return None
        if isinstance(resolved, str):
            return resolved
        if match.group(1).startswith("options."):
            return resolved  # typed value preserved; env post-pass handles bool coercion
        return str(resolved)  # Path / int from $data_dir etc.

    def _replace(match: re.Match[str]) -> str:
        return str(_resolve_placeholder(match.group(1), ctx, options, auth_token))

    return _PLACEHOLDER_RE.sub(_replace, value)


def _resolve_placeholder(
    key: str, ctx: ServiceContext, options: Mapping[str, Any], auth_token: Any
) -> Any:
    """Resolve a single placeholder key to its typed value.

    ``$data_dir`` / ``$state_dir`` / etc. return ``Path`` objects; the
    caller (``_resolve_string``) stringifies them. ``$options.X``
    returns the typed option value (bool, int, str, None) so
    downstream code can use it as a typed value (notably the auth
    block, which needs ``$options.jwt_enabled`` to round-trip as
    bool -- not a stringified ``"True"`` / ``"False"``).
    """
    if key == "state_dir":
        return ctx.state_dir
    if key == "data_dir":
        return ctx.data_dir
    if key == "vault_path":
        return ctx.vault_path
    if key == "media_vault_path":
        return ctx.media_vault_path
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

    Strings get the substitution (typed for single placeholder, string
    for mixed); ``Path`` objects get stringified so the recursive walk
    over a model_dump output (where ``Path`` survives as a Path
    object) still substitutes correctly; dicts and lists are walked;
    everything else is returned untouched.
    """
    if isinstance(value, str):
        return _resolve_string(value, ctx, options, auth_token)
    if isinstance(value, Path):
        # Path objects can survive ``model_dump`` with their string
        # form containing ``$variable`` markers (e.g. option defaults
        # like ``"$data_dir/.."``). Stringify so the substitution
        # path picks them up; the next pass replaces the marker.
        return _resolve_string(str(value), ctx, options, auth_token)
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
    option_specs: Mapping[str, _spec_module.OptionSpec],
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
        option_specs=option_specs,
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
    option_specs: Mapping[str, _spec_module.OptionSpec],
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
        option_specs=option_specs,
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

    Loading order (ADR-036):

    1. Parse YAML and validate the ``options:`` block in isolation --
       we need the typed options schema to resolve ``$options.X``
       markers elsewhere.
    2. Build the options model, probe it against ``ctx.options``, and
       resolve ``$variable`` markers inside option defaults (e.g.
       ``pictures_dir: { default: "$data_dir/.." }``).
    3. Substitute ``$variable`` markers in the *raw* spec dict so
       pydantic validation sees concrete values. This is what lets
       ``container.listen_port: "$options.web_port"`` validate as an
       ``int`` after substitution (the ADR-035 carve-out for
       ``listen_port`` / ``listen_host`` / ``internal_port`` /
       ``public_host`` is lifted here).
    4. Pydantic-validate the substituted dict against the spec
       union.
    5. Merge ``extra_env`` / ``extra_mounts`` options additively into
       ``env`` / ``volumes`` (user additions win on collisions).
    6. Build the kind-specific config dataclass and instantiate the
       service.

    Builders take the fully-resolved dict and produce dataclasses
    with no placeholder machinery of their own.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: top-level YAML must be a mapping, got {type(raw).__name__}")

    if raw.get("name") != path.stem:
        raise ValueError(
            f"{path}: filename stem {path.stem!r} does not match spec name {raw.get('name')!r}; "
            f"rename the file or fix the YAML's name field"
        )

    # Validate the options block first so we have a typed schema to
    # resolve ``$options.X`` references against. The full spec
    # validation happens after substitution below.
    raw_options = raw.get("options") or {}
    option_specs: dict[str, OptionSpec] = {
        name: OptionSpec.model_validate(spec) for name, spec in raw_options.items()
    }
    options_model = spec_to_options_model(
        option_specs, model_name=f"{path.stem.title()}YAMLOptions"
    )

    # Probe the merged options (user overrides > YAML defaults) so
    # ``$options.X`` resolves to the runtime value, not just the default.
    probe = options_model(**ctx.options)
    options_dump = probe.model_dump()
    # Resolve ``$variable`` markers in option defaults themselves, so
    # ``pictures_dir: { default: "$data_dir/.." }`` becomes a concrete
    # path before it's used as a lookup value for ``$options.X``
    # elsewhere in the spec.
    options_resolved = resolve_placeholders(options_dump, ctx, options_dump)

    # Substitute placeholders in the raw dict so pydantic sees concrete
    # values. ``container.listen_port: "$options.web_port"`` becomes
    # ``container.listen_port: 9090`` before validation.
    raw_substituted = resolve_placeholders(raw, ctx, options_resolved)

    try:
        spec: DockerServiceSpec | UvServiceSpec = _validate_spec(raw_substituted)
    except ValidationError as exc:
        raise ValueError(f"{path}: YAML failed schema validation:\n{exc}") from exc

    # The fully-resolved spec, used to feed the config builder.
    resolved = resolve_placeholders(spec.model_dump(), ctx, options_resolved)

    # Map options (``extra_env`` / ``extra_mounts``) merge additively
    # into the resolved spec, with user additions winning on key
    # collisions. The YAML ``env:`` / ``volumes:`` blocks declare the
    # typed knobs; the map options are the free-form long tail
    # (ADR-036). Values stay as strings so they match the static
    # YAML volumes; ``mount_map`` pydantic coercion produces Path
    # objects, so we stringify them back here.
    if isinstance(spec, DockerServiceSpec):
        extra_env = dict(options_resolved.get("extra_env") or {})
        extra_mounts_raw = options_resolved.get("extra_mounts") or {}
        extra_mounts = {k: str(v) for k, v in extra_mounts_raw.items()}
        merged_env = {**(resolved.get("env") or {}), **extra_env}
        # Coerce bool env values to lowercase strings so docker run
        # gets ``PHOTOPRISM_UPLOAD_NSFW=true`` (the conventional form)
        # rather than ``=True`` (Python's str repr). ``$options.X``
        # preserved the typed value through substitution; this pass
        # converts only the env dict.
        resolved["env"] = {
            k: ("true" if v is True else "false" if v is False else v)
            for k, v in merged_env.items()
        }
        resolved["volumes"] = {**(resolved.get("volumes") or {}), **extra_mounts}

    if isinstance(spec, DockerServiceSpec):
        config = _build_docker_config(resolved, options_model, spec.options)
        return DockerService(ctx, config=config)

    config = _build_uv_config(resolved, options_model, spec.options)
    return UvService(ctx, config=config)


__all__ = ["load_service_spec", "resolve_placeholders"]
