# ADR-035: Declarative service template — base classes + YAML spec for built-in docker and uv-tool services

## Title

Declarative service template — concrete `DockerService` and `UvService` base classes in `utils/services/`, plus a versioned YAML spec that builds the simple built-in services (crawl4ai, sillytavern, and future peers) from data instead of code. Complex services (llama-swap, comfyui) stay as Python plugins.

## Status

Proposed.

## Context

The service plugin surface has been pulled in two directions at once, and the codebase has accumulated the friction of both.

ADR-013 factored shared lifecycle plumbing into `utils/` (`TmuxProcess`, `HealthProbe`, `BackgroundInstallSession`, `render_service_controls`, `render_tail_log`) and ADR-024 added `DockerContainer` for docker plumbing. With those in place, a new docker service shouldn't need *any* subprocess or Streamlit code — only the service-specific bits. But today it still needs all of it, because there's no canonical "docker service" to extend.

The simple built-in services today are nearly identical at the structural level:

| axis | comfyui | crawl4ai | sillytavern | cptr |
|---|---|---|---|---|
| total service.py | 265 | 265 | 221 | 184 |
| total lifecycle.py | 117 | 110 | 106 | 147 |
| total install.py | 238 | 179 | 179 | 149 |
| total ui/status + image | ~250 | ~280 | ~210 | ~40 |
| tests | 1808 | 1047 | 298 | 818 |

What varies is small and mechanical: image repo / tag, container name, listen port, env vars, volume mounts, restart policy, a few capability flags, a category label, a description string, and a handful of optional pre-start hooks (comfyui seeds vault symlinks, crawl4ai generates an API token, sillytavern seeds a YAML whitelist). Everything else — the methods on `InferenceService`, the install lifecycle, the status page layout — is copy-paste.

Two services don't fit the simple shape and should keep custom code: **llama-swap** (recipes, generated config, agent export, built-in model entries) and **comfyui** (GPU dispatch via OCI runtime *or* CDI, vault symlinks, a custom Models page). Their complexity is intrinsic, not boilerplate.

The user wants to add several more services soon. Each one today is ~30 minutes of code + tests against a fixed scaffolding cost that's been paid five times already. The cost is felt by the *next* service, not the existing five.

### Forces

- **The framework/plugin boundary (ADR-009) must hold.** A new concrete `InferenceService` base class belongs in `utils/` (leaf package, plugins may import from it), not in `contracts/` (which carries the ABC only).
- **The plugin boundary test must keep working.** The Python boundary test walks `.py` files; YAML files are invisible to it by construction. A parallel test must enforce schema + constructibility for YAML services.
- **The default UI must remain useful** for YAML services without writing per-service Python. The crawl4ai status page today has an essential feature — the API token panel with a copy-to-clipboard button — that the default template must reproduce.
- **The versioned-settings story (ADR-006, ADR-034) extends here.** Adding `version: 1` to the YAML schema from day one is cheap; retrofitting migration when v2 lands is expensive.
- **Conversion is in scope, not deferred.** Dogfooding the new path on crawl4ai + sillytavern is the only way to know the abstraction is right. Comfyui and llama-swap stay Python because their complexity doesn't fit the canonical shape.

## Decision

We will introduce a **declarative service template** with three layers: concrete base classes for the two common service kinds (docker, uv-tool), a YAML spec for the simple cases, and a default UI status page that YAML services share.

### 1. New package — `genesis_worker/utils/services/`

A new leaf package holding the concrete base classes and their YAML machinery. `utils/` may import from `contracts/` (per ADR-013), but this new package imports only from `contracts/` and stdlib.

```
utils/services/
├── __init__.py        # public surface
├── base.py            # DeclarativeServiceBase — shared identity, public_host, contract methods
├── docker_service.py  # DockerService(DeclarativeServiceBase) + DockerServiceConfig + DockerImageInstall
├── uv_service.py      # UvService(DeclarativeServiceBase) + UvServiceConfig + UvToolInstall
├── spec.py            # DockerServiceSpec, UvServiceSpec, OptionSpec — pydantic, versioned
├── loader.py          # load_service_spec(path) → InferenceService
├── hooks.py           # Pre-start hook registry (seed_yaml_whitelist, ensure_persistent_token)
├── panels.py          # UI panel registry (service_info, container_info, auth_token, log_tail)
└── default_status.py  # render_default_status(svc) — the default status page template
```

### 2. `DeclarativeServiceBase` — shared base for both kinds

`DeclarativeServiceBase(InferenceService)` captures the cross-cutting behavior that doesn't vary between docker and uv-tool services. It sets identity (`name`, `display_name`, `dir_name`) from the config in `__init__`, validates `ctx.options` against the config's `options_model`, and implements the methods whose bodies are identical across kinds: `public_host`, `uninstall_installable`, `primary_installable`, `installs`, `ui_pages`, plus the path-resolution helpers used by `tail_log` and the default log/data paths.

Kind-specific behavior (`is_available`, `is_running`, `start`, `stop`, `status`, `wait_ready`, `tail_log`, `runtime_endpoint`, `web_ui_endpoint`) stays abstract on this base. `DockerService` and `UvService` fill those in.

This is a *base class*, not a mixin: the subclass relationship is "X is a declarative service." We name it that way to keep the intent clear.

### 3. `DockerService` and `DockerServiceConfig`

`DockerService(DeclarativeServiceBase)` adds the docker lifecycle on top of the shared base. Its lifecycle delegates to `DockerContainer` (already in `utils/process/docker.py`); the install delegates to `DockerImageInstall`, also driven by the same config. The base-class methods (`public_host`, `uninstall_installable`, `installs`, `ui_pages`, etc.) are inherited unchanged.

```python
@dataclass(frozen=True)
class DockerServiceConfig:
    name: str
    display_name: str
    description: str
    category: ServiceCategory
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate

    image_repo: str
    image_tag: str
    image_install_name: str            # shown on the "Image" page
    source_url: str | None

    container_name: str
    listen_host: str = "0.0.0.0"
    listen_port: int
    internal_port: int | None = None  # defaults to listen_port
    public_host: str | None = None
    web_ui_path: str = "/"            # appended to listen address for the Web UI button
    restart_policy: str = "unless-stopped"
    shm_size: str | None = None
    health_probe_path: str = "/"
    puid_default: bool = True
    pgid_default: bool = True

    runtime: str | None = None
    gpu_flags: list[str] | None = None

    extra_env: dict[str, str] = field(default_factory=dict)
    extra_volumes: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)

    options_model: type[BaseModel]               # pydantic model for ctx.options
    data_dir_subpath: str | None = "data"
    log_filename: str | None = None             # defaults to "<name>.log"

    auth: AuthConfig | None = None
    ui_panels: tuple[str, ...] = ()             # names from utils.services.panels
```

`AuthConfig` is a small frozen dataclass that powers both the env injection (`start()` reads it to set e.g. `CRAWL4AI_API_TOKEN`) and the `auth_token` UI panel:

```python
@dataclass(frozen=True)
class AuthConfig:
    enabled_option: str | None = None     # option name; when truthy, render "JWT enabled" instead
    token_env_var: str                    # injected as env var in start()
    token_file: Path                      # resolved against ctx.state_dir
    token_file_mode: int = 0o600
    token_generator: str                  # registered name in hooks.py
    fallback_option: str | None = None    # explicit option wins over the file
```

`DockerService` exposes `auth_token()` and `auth_enabled()` methods that read the config + the file + the fallback option, mirroring the current crawl4ai behavior. The `auth_token` panel renders based on these.

### 4. `UvService` and `UvServiceConfig`

`UvService(DeclarativeServiceBase)` is the same shape for services that install via `uv tool install` and run as a tmux-managed host process (cptr today, future peers). The lifecycle backend swaps to `TmuxProcess` + `HealthProbe`. The install backend is `UvToolInstall`. Subclass hook `_post_install()` lets cptr override it for its file patch — that's the only Python custom code cptr needs.

```python
@dataclass(frozen=True)
class UvServiceConfig:
    name: str
    display_name: str
    description: str
    category: ServiceCategory
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate

    package_name: str                     # installed via `uv tool install <package_name>`
    binary_name: str                      # on PATH after install
    command: list[str]                    # args after the binary, e.g. ["run", "--host", "0.0.0.0"]
    install_env: dict[str, str] = field(default_factory=dict)
    command_env: dict[str, str] = field(default_factory=dict)

    listen_host: str = "0.0.0.0"
    listen_port: int
    public_host: str | None = None
    health_probe_path: str = "/"
    health_timeout_s: float = 60.0
    session_name: str | None = None       # defaults to name
    graceful_stop_timeout_s: float = 10.0

    options_model: type[BaseModel]
    log_filename: str | None = None
```

`UvService.start()` builds the command from `binary_name + command`, wraps it in `TmuxProcess.start(cmd, log_file)` (which adds the `tee -a` pipe, ADR-013), and waits for HTTP readiness via `HealthProbe`. Stop and status use `TmuxProcess` directly.

### 5. YAML spec — versioned, pydantic-validated

Each declarative service ships as a YAML file under `genesis_worker/services/_declarative/<name>.yaml`. The loader parses + validates + constructs in one step.

```yaml
# genesis_worker/services/_declarative/crawl4ai.yaml
version: 1
kind: docker
name: crawl4ai
display_name: Crawl4AI
description: Web crawler + dashboard
category: crawler
capabilities:
  has_web_ui: true
  can_install: true
resource_estimate:
  vram_bytes_typical: 0
  vram_bytes_min: 0
  cpu_cores_recommended: 2

image:
  repo: unclecode/crawl4ai
  tag: latest
  install_name: crawl4ai
  source_url: https://hub.docker.com/r/unclecode/crawl4ai

container:
  name: crawl4ai
  listen_host: 0.0.0.0
  listen_port: 11235
  internal_port: 11235
  shm_size: 1g
  restart_policy: unless-stopped
  health_probe_path: /health
  web_ui_path: /playground/

env:
  CRAWL4AI_JWT_ENABLED: "$options.jwt_enabled"
  CRAWL4AI_API_TOKEN: "$auth.token"

volumes:
  /app/data: "$data_dir/data"

pre_start_hooks:
  - kind: ensure_persistent_token
    target: "$state_dir/api_token"
    mode: "0o600"
    generator: random_hex_32

auth:
  enabled_option: jwt_enabled
  token_env_var: CRAWL4AI_API_TOKEN
  token_file: "$state_dir/api_token"
  token_file_mode: "0o600"
  token_generator: random_hex_32
  fallback_option: api_token

ui:
  status:
    panels:
      - container_info
      - auth_token
      - log_tail

options:
  api_token: { type: string, optional: true, default: null }
  jwt_enabled: { type: bool, default: false }
  extra_args: { type: "list[string]", default: [] }
```

`sillytavern.yaml` is structurally similar but adds a `seed_yaml_whitelist` pre-start hook and has no `auth` block.

Pydantic models in `spec.py` mirror the YAML. `version: 1` is required and validated. Unknown top-level keys are rejected (pydantic `extra="forbid"`). The loader builds a `DockerServiceConfig` from the validated spec, resolves `$state_dir` / `$data_dir` / `$vault_path` / `$options.X` placeholders against the `ServiceContext`, generates the `options` pydantic model dynamically from the `options:` block via `pydantic.create_model()`, and instantiates the service.

### 6. Pre-start hook kinds

Two kinds ship in v1, both implemented in `utils/services/hooks.py`:

- **`seed_yaml_whitelist`** — lifted from `services/sillytavern/config.py`. Reads/writes a YAML file, ensures a list-valued key (e.g. `whitelist`) contains the host's connected subnets, the docker bridge gateway, and the Tailscale CGNAT range. Tailscale CGNAT moves to `utils/net/constants.py` as a module constant so other code can use it. Signature is generic: `target_path`, `key`, `extras`, `disable_docker_hosts`. The sillytavern `config.py` module is deleted; the YAML hook replaces it.
- **`ensure_persistent_token`** — for the crawl4ai case. Reads a file; if present returns its content; if absent, calls a registered generator (`random_hex_32`, `random_urlsafe_32`), persists atomically with the requested mode (`0o600` default), and returns the new value.

Adding a new hook kind = one function in `utils/services/hooks.py` + a registered name.

### 7. UI panels

Four panels ship in v1, all implemented in `utils/services/panels.py`:

- **`service_info`** — display name, start/stop/install controls via `render_service_controls`, Web UI link, resource estimate.
- **`container_info`** — image ref, container name, listen address, web UI URL. Docker-only; the loader omits it for uv services.
- **`auth_token`** — reads the `auth` config, renders the source note + copy-to-clipboard button. If `enabled_option` is truthy in the options, renders a "JWT enabled" info message instead. Reproduces the current crawl4ai behavior exactly.
- **`log_tail`** — `render_tail_log`.

The default status page is a single file `utils/services/default_status.py`. It iterates `svc.ui_panels` and calls each renderer in order. The default panel set for docker is `[service_info, container_info, log_tail]`; for uv services `[service_info, log_tail]`. Services that want the auth panel add it explicitly to their YAML `ui.status.panels` list — only crawl4ai does today.

### 8. `ServiceRegistry` walks Python + YAML

`ServiceRegistry.__init__` walks two sources, in order:

```python
# existing
for cls in _plugin_classes(_SERVICES_PKG, InferenceService):
    self._instances[cls.name] = build(self._context(cls))

# new
for spec_path in _declarative_spec_paths(_SERVICES_PKG + "/_declarative"):
    svc = load_service_spec(spec_path, context=self._context_from_path(spec_path))
    self._instances[svc.name] = svc

# duplicate-name guard
assert_python_and_yaml_disjoint(...)
```

The plugin boundary test is amended: it walks `.py` files only (YAML is invisible to it). A new parallel test, `test_declarative_specs.py`, walks every `.yaml` in `services/_declarative/` and asserts each one parses, constructs, and survives a smoke lifecycle check (`is_available`, `capabilities`, `category`, `description`).

### 9. Conversions

- **`services/crawl4ai/`** — DELETE. Replaced by `services/_declarative/crawl4ai.yaml`. The `ui/status.py`, `ui/image.py`, `service.py`, `lifecycle.py`, `install.py`, `options.py`, `acquire.py` are all gone.
- **`services/sillytavern/`** — DELETE. Replaced by `services/_declarative/sillytavern.yaml`. `config.py` is gone (replaced by `seed_yaml_whitelist` hook).
- **`services/cptr/`** — REWRITTEN as a thin Python subclass of `UvService` (~50 lines) that overrides `_post_install()` for the existing pi-agent timeout patch. Keeps custom code because the post-install patch isn't a generic hook.
- **`services/comfyui/`** — UNCHANGED. Its complexity (GPU dispatch + vault symlinks + Models page) doesn't fit the canonical shape.
- **`services/llama_swap/`** — UNCHANGED. Recipes + config generation + agent export are intrinsic.

The `tests/test_crawl4ai_*`, `tests/test_sillytavern_*`, and most of `tests/test_cptr_*` files are replaced by framework-level tests:

- `tests/test_docker_service_base.py` — base class
- `tests/test_uv_service_base.py` — base class
- `tests/test_yaml_loader.py` — schema + loader
- `tests/test_declarative_specs.py` — built-in YAMLs construct
- `tests/test_seed_yaml_whitelist.py` — hook
- `tests/test_ensure_persistent_file.py` — hook
- `tests/test_default_status_page.py` — panel renderers

The plugin boundary test gets a small amendment: empty `__init__.py` files in `services/_declarative/` (Python markers for `importlib.resources`) walk and pass; YAML files are not walked.

## Consequences

**Positive**

- New docker or uv-tool services ship as YAML files in source. ~30 minutes of YAML instead of ~30 minutes of Python+tests per service.
- `services/crawl4ai/` and `services/sillytavern/` shrink from ~700 combined lines of Python to two ~40-line YAML files. Net reduction even after adding the new infrastructure.
- The `auth_token` panel reproduces crawl4ai's API-token UX exactly; the operational requirement is met.
- `cptr` shrinks from 818 lines of tests + ~184 lines of service code to a thin subclass. The post-install pi-agent timeout patch becomes one method override.
- The framework/plugin boundary is preserved: `utils/services/` is a leaf package; YAML files don't violate the boundary by construction; the new base classes import only from `contracts/` and stdlib.
- Adding a new pre-start hook or UI panel is a single function in the appropriate registry, not a per-service change. The hook/panel system is the natural extension point.
- Versioned YAML (`version: 1` from day one) means v2 migration is a known problem, not a retrofit.
- Existing tests for `TmuxProcess`, `DockerContainer`, `HealthProbe`, `render_service_controls`, `render_tail_log` cover the new base classes' lifecycle. We don't write new lifecycle tests for behavior the framework already tests.

**Negative**

- The plugin-boundary concept is slightly fuzzier: YAML files aren't "plugins" in the AST sense, so the boundary test doesn't see them. We close the gap with `test_declarative_specs.py`. Anyone who reads the boundary test in isolation might miss that YAML services exist; we mitigate with a comment at the top of `test_plugin_boundary.py`.
- The `auth_token` panel is opinionated about how an API token is exposed (file + generator + env var). Services that want a different shape (e.g. an OAuth flow, a token from an external secrets manager) keep Python. We don't model the long tail in v1.
- The `options:` block in YAML is a tiny DSL (`type: string`, `type: bool`, `type: "list[string]"`, etc.). It's enough for the services we have today, but exotic option types (nested models, custom validators) need a Python `options.py` companion. We accept that as the escape hatch.
- `DockerServiceConfig.options_model: type[BaseModel]` is a hard requirement: every declarative service must declare its options schema. There's no way to have "untyped options." We considered loosening this but the cost of typed-but-validated options is small and the consistency benefit is real.
- The default status page can't express everything. Anything beyond the four shipped panels (custom graphs, multi-step wizards, the comfyui Models page) requires a Python subclass that overrides `ui_pages` to point at its own Streamlit files. This is the right escape hatch but it does mean the "YAML can do anything" mental model is wrong; we say "YAML for the canonical shape, Python for anything else."
- Test refactor: per-service construction tests (option defaults, attribute resolution, etc.) collapse into framework tests + YAML smoke tests. Anyone reading the old tests as documentation needs to follow the pointer to the YAML. Mitigated by YAML being self-documenting.

**Neutral**

- `services/_declarative/` is a new directory under `services/`. It's a sibling of the Python service packages but contains no Python (just an empty `__init__.py` and YAML files). `importlib.resources.files("genesis_worker.services._declarative")` resolves it reliably.
- The plugin discovery walker in `registries.py` gets a new helper `_declarative_spec_paths()`. The Python discovery path is unchanged.
- The user-added-YAMLs extension point (`<config_dir>/services.d/*.yaml`) is explicitly **out of scope** for this ADR. It can land later as a follow-up ADR once the YAML schema has stabilized in real use.
- `crawl4ai.yaml`'s `auth:` block duplicates one field with `env:` (`CRAWL4AI_API_TOKEN`). The duplication is intentional: `env:` declares what to inject; `auth:` declares where the value comes from. Different concerns, different fields.

## Plan

`docs/arch/plans/plan-035-declarative-service-template.md` — file-by-file execution in three independently testable phases:

1. **Base classes** — `utils/services/{base,docker_service,uv_service}.py`, the cptr conversion as proof.
2. **YAML infrastructure** — `utils/services/{spec,loader,hooks,panels,default_status}.py`, lifted utils (`seed_yaml_whitelist`, `ensure_persistent_file`), default UI.
3. **Declarative conversions** — `services/_declarative/{crawl4ai,sillytavern}.yaml`, deletion of the old Python modules, test consolidation.
