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
    ServiceContext,
)
from . import spec as _spec_module
from .base import DeclarativeServiceBase
from .docker_service import AuthConfig, DockerService, DockerServiceConfig
from .spec import (
    DockerServiceSpec,
    HookSpec,
    UvServiceSpec,
    spec_to_options_model,
)
from .uv_service import UvService, UvServiceConfig

_PLACEHOLDER_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)")


def _resolve_string(
    value: str, ctx: ServiceContext, options: Mapping[str, Any], auth_token: Any
) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return _resolve_placeholder(key, ctx, options, auth_token)

    return _PLACEHOLDER_RE.sub(_replace, value)


def _resolve_placeholder(
    key: str, ctx: ServiceContext, options: Mapping[str, Any], auth_token: Any
) -> str:
    if key == "state_dir":
        return str(ctx.state_dir)
    if key == "data_dir":
        return str(ctx.data_dir)
    if key == "vault_path":
        return str(ctx.vault_path)
    if key == "log_dir":
        return str(ctx.log_dir)
    if key == "cache_dir":
        return str(ctx.cache_dir)
    if key == "config_dir":
        return str(ctx.config_dir)
    if key.startswith("options."):
        opt_name = key.split(".", 1)[1]
        value = options.get(opt_name)
        return "" if value is None else str(value)
    if key == "auth.token":
        return "" if auth_token is None else str(auth_token)
    # Unknown placeholder -- leave the marker intact so callers can spot it.
    return f"${key}"


def resolve_placeholders(
    value: Any,
    ctx: ServiceContext,
    options: Mapping[str, Any],
    auth_token: Any = None,
) -> Any:
    """Recursively replace ``$state_dir`` / ``$options.X`` markers in ``value``.

    Strings get the substitution; dicts and lists are walked; everything
    else is returned untouched (Path, int, bool, None).
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
    spec: DockerServiceSpec,
    options_model: type[BaseModel],
    ctx: ServiceContext,
) -> DockerServiceConfig:
    """Build a ``DockerServiceConfig`` from a validated ``DockerServiceSpec``.

    Placeholders in env / volumes / auth.token_file / option defaults
    are resolved against ``ctx``. Hook entries stay as opaque dicts --
    the handler that consumes them parses the fields it cares about.
    """
    # We pass a partially-built options instance through resolve_placeholders
    # so option defaults that reference $state_dir etc. resolve to real paths.
    probe_options = options_model()
    resolved_options_dict = probe_options.model_dump()

    auth_token_value: Any = None
    if spec.auth is not None and spec.auth.fallback_option is not None:
        # Auth placeholders typically want the on-disk token or the
        # fallback option. The fallback option is the option itself
        # (``options.<name>``), so it's already reachable via that path.
        auth_token_value = resolved_options_dict.get(spec.auth.fallback_option)

    env = resolve_placeholders(dict(spec.env), ctx, resolved_options_dict, auth_token_value)
    volumes_resolved = resolve_placeholders(
        dict(spec.volumes), ctx, resolved_options_dict, auth_token_value
    )

    auth_cfg: AuthConfig | None = None
    if spec.auth is not None:
        token_file_str = resolve_placeholders(
            spec.auth.token_file, ctx, resolved_options_dict, auth_token_value
        )
        auth_cfg = AuthConfig(
            enabled_option=spec.auth.enabled_option,
            token_env_var=spec.auth.token_env_var,
            token_file=Path(token_file_str),
            token_file_mode=_spec_module._parse_mode(spec.auth.token_file_mode),
            token_generator=spec.auth.token_generator,
            fallback_option=spec.auth.fallback_option,
        )

    return DockerServiceConfig(
        name=spec.name,
        display_name=spec.display_name,
        description=spec.description,
        category=spec.category,
        capabilities=spec.capabilities,
        resource_estimate=spec.resource_estimate,
        options_model=options_model,
        log_filename=spec.log_filename,
        ui_pages=tuple(spec.ui.status_panels),
        # docker-specific
        image_repo=spec.image.repo,
        image_tag=spec.image.tag,
        image_install_name=spec.image.install_name or spec.name,
        source_url=spec.image.source_url,
        container_name=spec.container.name,
        listen_host=spec.container.listen_host,
        listen_port=spec.container.listen_port,
        internal_port=spec.container.internal_port,
        public_host=spec.container.public_host,
        web_ui_path=spec.container.web_ui_path,
        restart_policy=spec.container.restart_policy,
        shm_size=spec.container.shm_size,
        health_probe_path=spec.container.health_probe_path,
        puid_default=spec.container.puid_default,
        pgid_default=spec.container.pgid_default,
        runtime=spec.container.runtime,
        gpu_flags=spec.container.gpu_flags,
        extra_env=env,
        extra_volumes=volumes_resolved,
        extra_args=[],
        data_dir_subpath=spec.data_dir_subpath,
        pre_start_hooks=tuple(_hook_entry(h) for h in spec.pre_start_hooks),
        auth=auth_cfg,
    )


def _hook_entry(h: HookSpec) -> dict:
    """Convert a ``HookSpec`` (which uses ``extra=\"allow\"``) into a plain dict.

    ``kind`` and any per-hook fields land in the dict the handler
    receives; the handler validates the per-kind schema itself.
    """
    return h.model_dump()


def _build_uv_config(
    spec: UvServiceSpec,
    options_model: type[BaseModel],
    ctx: ServiceContext,
) -> UvServiceConfig:
    probe_options = options_model()
    resolved_options_dict = probe_options.model_dump()
    install_env = resolve_placeholders(dict(spec.install_env), ctx, resolved_options_dict, None)
    command_env = resolve_placeholders(dict(spec.command_env), ctx, resolved_options_dict, None)
    return UvServiceConfig(
        name=spec.name,
        display_name=spec.display_name,
        description=spec.description,
        category=spec.category,
        capabilities=spec.capabilities,
        resource_estimate=spec.resource_estimate,
        options_model=options_model,
        log_filename=spec.log_filename,
        ui_pages=tuple(spec.ui.status_panels),
        # uv-specific
        package_name=spec.package_name,
        binary_name=spec.binary_name,
        command=list(spec.command),
        install_env=install_env,
        command_env=command_env,
        listen_host=spec.listen_host,
        listen_port=spec.listen_port,
        public_host=spec.public_host,
        health_probe_path=spec.health_probe_path,
        health_timeout_s=spec.health_timeout_s,
        session_name=spec.session_name,
        graceful_stop_timeout_s=spec.graceful_stop_timeout_s,
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

    if isinstance(spec, DockerServiceSpec):
        config = _build_docker_config(spec, options_model, ctx)
        return DockerService(ctx, config=config)

    config = _build_uv_config(spec, options_model, ctx)
    return UvService(ctx, config=config)


__all__ = ["load_service_spec", "resolve_placeholders"]
