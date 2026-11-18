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

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, create_model, model_validator

from ...contracts import (
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
)
from ..ports.markers import AUTO_PORT_MARKER_PATTERN, is_marker

# A host port: a fixed integer, or a ``$auto_port.<name>`` marker asking the
# framework to allocate one at start time (ADR-043).
#
# The numeric constraints sit on the *int member*, not on the field: a
# constraint on the union raises a ``TypeError`` (not a ``ValidationError``)
# when a marker is validated, which the loader's ``except ValidationError``
# would not catch. The marker branch is constrained by pattern, so a plain
# string ("8096") fails it and falls through to the int branch, which coerces
# it in lax mode — user overrides stay integers.
PortValue = (
    Annotated[int, Field(gt=0, lt=65536)]
    | Annotated[str, StringConstraints(pattern=AUTO_PORT_MARKER_PATTERN)]
)

# A DNS label: lowercase letters, digits and dashes; no leading or
# trailing dash; at most 63 characters. A canonical name and a port's
# role label both end up in a hostname, so both are constrained by it.
DNS_LABEL_PATTERN = r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"
_DNS_LABEL_RE = re.compile(DNS_LABEL_PATTERN)


def is_dns_label(value: str) -> bool:
    """True iff ``value`` is a DNS label usable as one hostname label.

    ``fullmatch`` on purpose: ``$`` also matches just before a trailing
    newline, so ``"genesis\n"`` must not pass as a label.
    """
    return bool(value) and len(value) <= 63 and _DNS_LABEL_RE.fullmatch(value) is not None


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


class ExtraPortSpec(BaseModel):
    """One extra published port of a docker container.

    ``container`` is the port inside the container; ``host`` is the port on
    the node — a fixed integer or a ``$auto_port.<name>`` marker (ADR-043).
    """

    model_config = ConfigDict(extra="forbid")

    container: int
    host: PortValue


class PortSpec(BaseModel):
    """One routable port of a service (ADR-051).

    The top-level ``ports:`` list replaces the per-kind port fields. Each
    entry names the port's role (``label``), marks at most one of them
    ``primary``, and says which host port a client connects to (``host``)
    and which port the container itself listens on (``container``).
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    primary: bool = False
    host: PortValue
    container: int | None = None


def _validate_canonical_name(service: str, canonical_name: str | None) -> None:
    """The declared canonical name must be a DNS label (ADR-051)."""
    if canonical_name is None:
        return
    if not is_dns_label(canonical_name):
        raise ValueError(f"canonical_name {canonical_name!r} is not a DNS label")


def _validate_declared_ports(
    *,
    service: str,
    kind: str,
    ports: list[PortSpec] | None,
    legacy_present: Sequence[str],
    legacy_required: str = "",
) -> None:
    """The cross-field rules of the ``ports:`` block (ADR-051).

    ``ports:`` is the single source of truth when it is present, so the
    legacy per-kind port fields must be absent; exactly one port is the
    primary; a multi-port service labels every port (the labels keep the
    per-port hostnames apart); labels are DNS labels; and the host ports
    are distinct. Without a ``ports:`` block the legacy field is still
    required — the port must be declared somewhere.
    """
    if ports is None:
        if legacy_required and legacy_required not in legacy_present:
            raise ValueError(
                f"service {service!r} declares no `ports:` block and no {legacy_required} — "
                "declare the port in one place"
            )
        return
    if not ports:
        raise ValueError(f"service {service!r} declares an empty `ports:` block")
    if legacy_present:
        raise ValueError(
            f"service {service!r} declares `ports:` and {', '.join(legacy_present)} — "
            "declare the ports in one place"
        )
    primaries = [port for port in ports if port.primary]
    if len(primaries) != 1:
        raise ValueError(
            f"service {service!r} declares {len(primaries)} primary ports; exactly one is required"
        )
    labels: set[str] = set()
    for port in ports:
        if port.label is None:
            if len(ports) > 1:
                raise ValueError(
                    f"service {service!r} declares {len(ports)} ports; each needs a label"
                )
            continue
        if not is_dns_label(port.label):
            raise ValueError(f"port label {port.label!r} is not a DNS label")
        if port.label in labels:
            raise ValueError(f"service {service!r} declares the port label {port.label!r} twice")
        labels.add(port.label)
    hosts: set[int] = set()
    for port in ports:
        if isinstance(port.host, bool) or not isinstance(port.host, int):
            continue
        if port.host in hosts:
            raise ValueError(f"service {service!r} declares the host port {port.host} twice")
        hosts.add(port.host)
    if kind == "uv" and len(ports) > 1:
        raise ValueError(
            f"the uv kind publishes one port; service {service!r} declares {len(ports)}"
        )
    if kind == "docker":
        for index, port in enumerate(ports):
            if is_marker(port.host) and port.container is None:
                raise ValueError(
                    f"service {service!r} declares a marker host port without a container port "
                    f"(ports[{index}].container) — the container port cannot default to an "
                    "allocated host port"
                )


class ContainerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    listen_host: str = "0.0.0.0"
    listen_port: PortValue | None = None
    internal_port: int | None = None
    extra_ports: list[ExtraPortSpec] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def _require_internal_port_for_marker(self) -> ContainerSpec:
        # A marker means the host port is unknown until start time, so the
        # container port cannot default to it — the spec has to say which
        # port the container itself listens on.
        if is_marker(self.listen_port) and self.internal_port is None:
            raise ValueError(
                "container.internal_port is required when container.listen_port is a "
                "$auto_port marker: the container port cannot default to an "
                "allocated host port"
            )
        return self


class HookSpec(BaseModel):
    """One entry in ``pre_start_hooks:``.

    The framework keeps the per-kind payload opaque (``kind``-keyed
    dicts). Validation lives in the hook handler that consumes the
    entry, not here.

    ``key`` is the one shared, typed field across hook kinds:

    - ``materialize_orchestrator_config`` -- when set, the handler writes
      ``config[key]`` (a slice of the orchestrator-supplied config body)
      to ``target`` instead of the whole body. That lets one config dict
      be split across several files (e.g. prometheus: server config,
      file-SD target list, alert rules).
    - ``seed_yaml_whitelist`` -- the YAML key to seed (default
      ``whitelist``).

    Left unset, the entry behaves exactly as before.
    """

    model_config = ConfigDict(extra="allow")

    kind: str
    key: str | None = None


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

    # The cluster-wide canonical label this service answers to (ADR-051).
    # Declaring it asserts that the service is cluster-wide and
    # single-instance.
    canonical_name: str | None = None

    # The service's routable ports (ADR-051). Absent = the legacy
    # single-port form.
    ports: list[PortSpec] | None = None

    env: dict[str, Any] = Field(default_factory=dict)
    volumes: dict[str, Any] = Field(default_factory=dict)
    # ``extra_args`` is appended to the image's argv as ``command`` in
    # ``docker run``. It REPLACES the image's CMD, so a YAML that
    # declares ``extra_args`` must pass every argument the container
    # needs (e.g. ``serve`` for an image whose CMD is empty).
    extra_args: list[str] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def _validate_ports(self) -> DockerServiceSpec:
        legacy = [
            field
            for field, present in (
                ("container.listen_port", self.container.listen_port is not None),
                ("container.internal_port", self.container.internal_port is not None),
                ("container.extra_ports", bool(self.container.extra_ports)),
            )
            if present
        ]
        _validate_declared_ports(
            service=self.name,
            kind="docker",
            ports=self.ports,
            legacy_present=legacy,
            legacy_required="container.listen_port",
        )
        _validate_canonical_name(self.name, self.canonical_name)
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
    listen_port: PortValue | None = None
    public_host: str | None = None
    health_probe_path: str = "/"
    health_timeout_s: float = 60.0
    session_name: str | None = None
    graceful_stop_timeout_s: float = 10.0

    # The cluster-wide canonical label this service answers to (ADR-051).
    canonical_name: str | None = None

    # The service's routable ports (ADR-051). Absent = the legacy
    # single-port form.
    ports: list[PortSpec] | None = None

    log_filename: str | None = None
    ui: UiSpec = Field(default_factory=UiSpec)
    options: dict[str, OptionSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_option_types(self) -> UvServiceSpec:
        spec_to_options_model(self.options, model_name=f"{self.name.title()}Options")
        return self

    @model_validator(mode="after")
    def _validate_ports(self) -> UvServiceSpec:
        _validate_declared_ports(
            service=self.name,
            kind="uv",
            ports=self.ports,
            legacy_present=["listen_port"] if self.listen_port is not None else [],
            legacy_required="listen_port",
        )
        _validate_canonical_name(self.name, self.canonical_name)
        return self


class ComposeImageSpec(BaseModel):
    """One image in a compose stack (framework-level, not a user option).

    Every image the stack uses must be declared here. ``repo`` is the pull
    reference (registry path); ``default_tag`` is used when the user has not
    picked a version — a recorded install selection overrides it at render
    time. Image identity is owned by the framework, so it is not exposed in
    the options panel (reserved for app-tunable settings).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    repo: str
    default_tag: str = "latest"


class ComposeServiceSpec(BaseModel):
    """The full compose-kind YAML, validated against ``extra="forbid"``.

    The ``compose_template`` field is a plain string populated by the
    standard ``$placeholder`` mechanism (``$state_dir``, ``$data_dir``,
    ``$options.X``). The loader resolves every placeholder against the
    runtime context *before* storing the template, so the stored string
    is final — written to disk verbatim at start time.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    kind: Literal["compose"]
    name: str
    display_name: str
    description: str = ""
    category: ServiceCategory = ServiceCategory.OTHER
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate

    # Compose template — plain string, $placeholder-resolved by the loader
    # (declared image tag markers stay for render-time substitution).
    compose_template: str
    compose_version: str = "3.8"

    # Every image the stack uses. Each entry gets its own DockerImageInstall
    # (real registry versioning, GUI version pick). Rule: if it's in the
    # template, it's declared here.
    images: list[ComposeImageSpec] = Field(default_factory=list)

    # Port / health. Optional at top level: when the service declares a
    # ``listen_port`` option (the norm), the loader derives the runtime
    # port from the resolved option so there is a single source of truth.
    listen_host: str = "0.0.0.0"
    listen_port: PortValue | None = None
    health_probe_path: str = "/"

    # The cluster-wide canonical label this service answers to (ADR-051).
    canonical_name: str | None = None

    # The service's routable ports (ADR-051). Absent = the legacy
    # single-port form.
    ports: list[PortSpec] | None = None

    # Top-level compose env / volumes (project-wide, not per-service)
    env: dict[str, Any] = Field(default_factory=dict)
    volumes: dict[str, Any] = Field(default_factory=dict)

    pre_start_hooks: list[HookSpec] = Field(default_factory=list)
    ui: UiSpec = Field(default_factory=UiSpec)
    options: dict[str, OptionSpec] = Field(default_factory=dict)

    data_dir_subpath: str | None = "data"
    log_filename: str | None = None

    @model_validator(mode="after")
    def _validate_option_types(self) -> ComposeServiceSpec:
        spec_to_options_model(self.options, model_name=f"{self.name.title()}Options")
        return self

    @model_validator(mode="after")
    def _validate_ports(self) -> ComposeServiceSpec:
        _validate_declared_ports(
            service=self.name,
            kind="compose",
            ports=self.ports,
            legacy_present=["listen_port"] if self.listen_port is not None else [],
        )
        _validate_canonical_name(self.name, self.canonical_name)
        return self


ServiceSpecUnion = Annotated[
    DockerServiceSpec | UvServiceSpec | ComposeServiceSpec,
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
    """A port option: a fixed ``int`` in ``(0, 65536)`` or an auto-port marker.

    Optional ports allow ``None``; required ports reject 0 and values in
    the ephemeral range. The constraint fires at options-validation
    time, so a bad user override fails loudly rather than reaching the
    container's port mapper.

    The numeric constraint lives on the int member of the union (see
    :data:`PortValue`); the field itself carries only the default.
    """
    if optional:
        return (PortValue | None, default)
    base = default if default is not None else 0
    return (PortValue, base)


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
    "ComposeImageSpec",
    "ComposeServiceSpec",
    "ContainerSpec",
    "DockerServiceSpec",
    "ExtraPortSpec",
    "HookSpec",
    "ImageSpec",
    "OptionSpec",
    "PortSpec",
    "PortValue",
    "ServiceSpecUnion",
    "UiSpec",
    "UvServiceSpec",
    "_parse_mode",
    "is_dns_label",
    "spec_to_options_model",
]
