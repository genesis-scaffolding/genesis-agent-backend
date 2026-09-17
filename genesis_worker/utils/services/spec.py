"""Pydantic schemas for the declarative-service YAML spec.

The YAML loader (see ``loader.py``) parses a ``*.yaml`` file into one
of these models, validates every field (``extra="forbid"`` keeps the
schema honest), and constructs a ``DockerServiceConfig`` /
``UvServiceConfig`` from the resolved form. ``version`` is a literal
type so a v2 file is rejected at parse time -- not later, when the
loader would have to guess what changed.

Adding a new option type: add a branch to ``_OPTION_TYPE_BUILDERS``
and the corresponding YAML literal. Adding a new hook or panel kind
is a registry change (``hooks.py`` / ``panels.py``), not a spec change.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from ...contracts import (
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
)


class OptionSpec(BaseModel):
    """One entry in the YAML's ``options:`` block.

    ``type`` is a tiny DSL: ``string``, ``int``, ``float``, ``bool``,
    ``path``, ``port``, ``env_map``, ``mount_map``, ``list[string]``,
    ``list[int]``, ``list[path]``. Exotic option types (nested models,
    custom validators) need a Python ``options.py`` companion; the
    YAML DSL intentionally stops short.

    ``ui_label``, ``ui_help``, ``ui_group`` are UI metadata consumed by
    the ``configure`` panel. Free-form strings; the panel renders
    ``ui_group`` as the section heading and falls back to the option
    name when ``ui_label`` is empty (ADR-036).
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    optional: bool = False
    default: Any = None
    ui_label: str = ""
    ui_help: str = ""
    ui_group: str = ""


class ImageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    tag: str = "latest"
    install_name: str = ""
    source_url: str | None = None


class ContainerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    listen_host: str = "0.0.0.0"
    listen_port: int
    internal_port: int | None = None
    public_host: str | None = None
    web_ui_path: str = "/"
    restart_policy: str = "unless-stopped"
    shm_size: str | None = None
    health_probe_path: str = "/"
    puid_default: bool = True
    pgid_default: bool = True
    runtime: str | None = None
    gpu_flags: list[str] | None = None
    security_opts: list[str] | None = None


class HookSpec(BaseModel):
    """One entry in ``pre_start_hooks:``.

    The framework keeps the per-kind payload opaque (``kind``-keyed
    dicts). Validation lives in the hook handler that consumes the
    entry, not here.
    """

    model_config = ConfigDict(extra="allow")

    kind: str


class AuthSpec(BaseModel):
    """Auth block schema. ``enabled_option`` / ``fallback_option`` accept
    any type because the loader substitutes ``$options.X`` references
    to their typed values (bool / int / str / None) before pydantic
    validates the spec (ADR-036). The docker config builder then
    coerces these to the right shape (``bool`` for ``enabled``,
    ``str | None`` for ``fallback``).
    """

    model_config = ConfigDict(extra="forbid")

    enabled_option: Any | None = None
    token_env_var: str = ""
    token_file: str  # may reference $state_dir/$data_dir
    token_file_mode: str = "0o600"
    token_generator: str = "random_hex_32"
    fallback_option: Any | None = None


class UiSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_panels: list[str] = Field(default_factory=list)


class DockerServiceSpec(BaseModel):
    """The full docker-kind YAML, validated against ``extra="forbid"``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    kind: Literal["docker"]
    name: str
    display_name: str
    description: str = ""
    category: ServiceCategory = ServiceCategory.OTHER
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate

    image: ImageSpec
    container: ContainerSpec

    env: dict[str, Any] = Field(default_factory=dict)
    volumes: dict[str, Any] = Field(default_factory=dict)
    pre_start_hooks: list[HookSpec] = Field(default_factory=list)
    auth: AuthSpec | None = None
    ui: UiSpec = Field(default_factory=UiSpec)
    options: dict[str, OptionSpec] = Field(default_factory=dict)

    data_dir_subpath: str | None = "data"
    log_filename: str | None = None

    @model_validator(mode="after")
    def _validate_option_types(self) -> DockerServiceSpec:
        # Build the options model early so unknown type strings fail at
        # ``model_validate`` rather than later in ``load_service_spec``.
        spec_to_options_model(self.options, model_name=f"{self.name.title()}Options")
        return self


class UvServiceSpec(BaseModel):
    """The full uv-kind YAML, validated against ``extra="forbid"``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    kind: Literal["uv"]
    name: str
    display_name: str
    description: str = ""
    category: ServiceCategory = ServiceCategory.OTHER
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate

    package_name: str
    binary_name: str
    command: list[str] = Field(default_factory=list)
    install_env: dict[str, Any] = Field(default_factory=dict)
    command_env: dict[str, Any] = Field(default_factory=dict)

    listen_host: str = "0.0.0.0"
    listen_port: int
    public_host: str | None = None
    health_probe_path: str = "/"
    health_timeout_s: float = 60.0
    session_name: str | None = None
    graceful_stop_timeout_s: float = 10.0

    log_filename: str | None = None
    ui: UiSpec = Field(default_factory=UiSpec)
    options: dict[str, OptionSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_option_types(self) -> UvServiceSpec:
        spec_to_options_model(self.options, model_name=f"{self.name.title()}Options")
        return self


ServiceSpecUnion = Annotated[
    DockerServiceSpec | UvServiceSpec,
    Field(discriminator="kind"),
]


# --- options-model synthesis ----------------------------------------------


def _parse_mode(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("0o"):
            return int(text, 8)
        return int(text, 8)
    return 0o600


def _build_path_field(optional: bool, default: Any) -> Any:
    """``Path`` field accepts str-or-Path; pydantic coerces.

    Path defaults may carry ``$variable`` markers (e.g.
    ``"$data_dir/.."``); the loader resolves those before constructing
    the model so pydantic coerces the resolved string, not the raw
    marker (ADR-036).
    """
    coerced = Path(default) if isinstance(default, str) else default
    if optional:
        return (Path | None, coerced)
    return (Path, coerced)


def _build_list_string_field(optional: bool, default: Any) -> Any:
    if optional:
        return (list[str] | None, default)
    return (list[str], default or [])


def _build_list_int_field(optional: bool, default: Any) -> Any:
    if optional:
        return (list[int] | None, default)
    return (list[int], default or [])


def _build_list_path_field(optional: bool, default: Any) -> Any:
    default_value = [Path(p) for p in (default or [])] if isinstance(default, list) else []
    if optional:
        return (list[Path] | None, default_value)
    return (list[Path], default_value)


def _build_scalar_field(type_name: str, optional: bool, default: Any) -> Any:
    if type_name == "string":
        if optional:
            return (str | None, default)
        return (str, default if default is not None else "")
    if type_name == "int":
        if optional:
            return (int | None, default)
        return (int, default if default is not None else 0)
    if type_name == "float":
        if optional:
            return (float | None, default)
        return (float, default if default is not None else 0.0)
    if type_name == "bool":
        return (bool, bool(default))
    raise ValueError(f"unknown option type {type_name!r}")


def _build_port_field(optional: bool, default: Any) -> Any:
    """``int`` constrained to ``(0, 65536)`` via pydantic Field constraints.

    Optional ports allow ``None``; required ports reject 0 and values
    in the ephemeral range. The constraint fires at options-validation
    time, so a bad user override fails loudly rather than reaching the
    container's port mapper.
    """
    if optional:
        return (int | None, default)
    base = default if default is not None else 0
    return (int, Field(base, gt=0, lt=65536))


def _build_env_map_field(optional: bool, default: Any) -> Any:
    """``dict[str, str]`` — additive raw env vars for a docker service.

    Optional by convention (the long tail is opt-in). Coerces YAML
    mappings; JSON sidecar values pass through unchanged.
    """
    if optional:
        return (dict[str, str] | None, default or {})
    return (dict[str, str], default or {})


def _build_mount_map_field(optional: bool, default: Any) -> Any:
    """``dict[str, Path]`` — additive bind mounts (container -> host).

    The container path stays a string (docker accepts any string);
    the host path is coerced to ``Path`` so it matches the static
    volumes' type and downstream mkdir/chown works on a Path.
    """
    default_paths = {k: Path(v) for k, v in default.items()} if isinstance(default, dict) else {}
    if optional:
        return (dict[str, Path] | None, default_paths)
    return (dict[str, Path], default_paths)


_OPTION_TYPE_BUILDERS: dict[str, Callable[[bool, Any], Any]] = {
    "string": lambda opt, default: _build_scalar_field("string", opt, default),
    "int": lambda opt, default: _build_scalar_field("int", opt, default),
    "float": lambda opt, default: _build_scalar_field("float", opt, default),
    "bool": lambda opt, default: _build_scalar_field("bool", opt, default),
    "path": lambda opt, default: _build_path_field(opt, default),
    "port": lambda opt, default: _build_port_field(opt, default),
    "env_map": lambda opt, default: _build_env_map_field(opt, default),
    "mount_map": lambda opt, default: _build_mount_map_field(opt, default),
    "list[string]": lambda opt, default: _build_list_string_field(opt, default),
    "list[int]": lambda opt, default: _build_list_int_field(opt, default),
    "list[path]": lambda opt, default: _build_list_path_field(opt, default),
}


def spec_to_options_model(
    options: dict[str, OptionSpec],
    *,
    model_name: str = "YAMLOptions",
) -> type[BaseModel]:
    """Build a pydantic ``BaseModel`` from the YAML's ``options:`` block.

    Each entry's ``type`` is mapped to a python type via
    ``_OPTION_TYPE_BUILDERS``; ``optional`` adds ``| None``; ``default``
    becomes the field default. Unknown type strings raise -- the YAML
    schema is intentionally closed.
    """
    fields: dict[str, Any] = {}
    for name, spec in options.items():
        builder = _OPTION_TYPE_BUILDERS.get(spec.type)
        if builder is None:
            raise ValueError(
                f"unknown option type {spec.type!r} for {name!r}; "
                f"supported: {sorted(_OPTION_TYPE_BUILDERS)}"
            )
        fields[name] = builder(spec.optional, spec.default)
    return create_model(model_name, **fields)


__all__ = [
    "AuthSpec",
    "ContainerSpec",
    "DockerServiceSpec",
    "HookSpec",
    "ImageSpec",
    "OptionSpec",
    "ServiceSpecUnion",
    "UiSpec",
    "UvServiceSpec",
    "_parse_mode",
    "spec_to_options_model",
]
