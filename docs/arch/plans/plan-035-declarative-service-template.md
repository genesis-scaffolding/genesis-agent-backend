# Plan: ADR-035 — declarative service template

Implements ADR-035. Three independently committable phases. Each phase
ends with the test/type/lint gate green and a runnable worker.

## Phase 1 — Base classes

Pure refactor. No YAML yet, no behavioral changes for any existing
service. `cptr` becomes the first adopter of `UvService` and proves
the base class works end-to-end.

### 1.1 New package skeleton

**`genesis_worker/utils/services/__init__.py`** — re-exports the
public surface:

```python
from .docker_service import DockerService, DockerServiceConfig, DockerImageInstall, AuthConfig
from .uv_service import UvService, UvServiceConfig, UvToolInstall
from .loader import load_service_spec

__all__ = [...]
```

**`genesis_worker/utils/services/base.py`** — shared base class
`DeclarativeServiceBase(InferenceService)`. Captures the
cross-cutting behavior that doesn't vary between docker and uv-tool
services:

- `__init__(self, ctx, *, config)` sets `self.name`,
  `self.display_name`, `self.dir_name` (auto-derived
  `name.replace("_", "-")`) from the config; validates
  `ctx.options` against `config.options_model`; resolves the
  default data_dir / log_file paths.
- `public_host()`, `uninstall_installable()`,
  `primary_installable()`, `installs()`, the `ui_pages` property —
  all implemented once, inherited by both `DockerService` and
  `UvService`.

`is_available`, `is_running`, `start`, `stop`, `status`,
`wait_ready`, `tail_log`, `runtime_endpoint`, `web_ui_endpoint`
stay abstract (declared as `@abstractmethod`). `DockerService` and
`UvService` fill those in.

This is a base class (subclass relationship: "X is a declarative
service"), not a mixin. Naming it that way keeps the intent clear.

### 1.2 `DockerService` + config + install

**`genesis_worker/utils/services/docker_service.py`** — concrete
`DockerService(DeclarativeServiceBase)` class. Adds the docker
lifecycle on top of the shared base.

- `__init__(self, ctx, *, config: DockerServiceConfig)` — delegates
  identity / option validation / path resolution to
  `super().__init__`; sets up `auth_token_path` (resolved against
  `ctx.state_dir`) when the config carries an `AuthConfig`.
- `capabilities`, `resource_estimate`, `category`, `description`,
  `is_available`, `is_running`, `status`, `wait_ready`, `stop`,
  `tail_log`, `runtime_endpoint`, `web_ui_endpoint`, `public_host`,
  `uninstall_installable`, `installs`, `primary_installable`,
  `ui_pages` — all implemented, all delegating to
  `DockerContainer` / `HealthProbe`.
- `start()` — calls pre-start hooks (in declared order), then
  composes env from `config.extra_env` + `AuthConfig` + PUID/PGID,
  volumes from `config.extra_volumes` (path-resolved), `extra_args`,
  and delegates to `DockerContainer.run()`.
- `auth_token()`, `auth_enabled()` — read `config.auth` + the file +
  the fallback option. Idempotent — generate only on `start()`.

**`DockerImageInstall(ServiceInstall)`** — replaces
`Crawl4AiImage`, `SillyTavernImage`, `ComfyUiImage` (the latter only
if its arch filter survives; otherwise comfyui keeps its own install).
Drives `DockerPullAcquireSession` + the 15-min tag cache. Takes the
same config dataclass; arch filter is an optional callable passed in
the config.

### 1.3 `UvService` + config + install

**`genesis_worker/utils/services/uv_service.py`** — concrete
`UvService(DeclarativeServiceBase)`.

- `__init__(self, ctx, *, config: UvServiceConfig)` — delegates to
  `super().__init__`; resolves the default session name and log file.
- Lifecycle: `start()` wraps the configured `command` in
  `TmuxProcess.start(cmd, log_file)` and waits via `HealthProbe`.
  `stop()` sends SIGINT then hard-kills via `TmuxProcess.stop()`
  (which already has the child-drain pattern from ADR-013).
- `tail_log()` reads from the resolved log file (same pattern as
  cptr today).
- `is_available()` checks `shutil.which(binary_name)`.
- `installs()` returns one `UvToolInstall`.
- Subclass hook `_post_install()` is a no-op default; cptr overrides
  it.

**`UvToolInstall(ServiceInstall)`** — drives
`UvToolAcquireSession` (already in `utils/acquire/uv_tool.py`).
State probes `shutil.which()`. `available_versions()` queries PyPI
JSON. `uninstall()` shells `uv tool uninstall`.

### 1.4 cptr conversion

**`genesis_worker/services/cptr/service.py`** — rewrite as a thin
subclass:

```python
class CptrService(UvService):
    name = "cptr"
    display_name = "Open WebUI Computer"
    category = ServiceCategory.CHAT
    description = "Open WebUI automation"

    def __init__(self, ctx: ServiceContext) -> None:
        opts = CptrOptions(**ctx.options)
        config = UvServiceConfig(
            name="cptr",
            display_name=self.display_name,
            ...
            package_name="cptr",
            binary_name="cptr",
            command=["run", "--host", "$listen_host", "--port", "$listen_port"],
            install_env={"CPTR_STREAM_READ_TIMEOUT": str(opts.stream_timeout_s), ...},
            command_env=...,
            ...
            options_model=CptrOptions,
        )
        super().__init__(ctx, config=config)
        self._install = ...  # UvToolInstall

    def _post_install(self) -> None:
        # cptr's pi-agent timeout patch
        ...
```

**`genesis_worker/services/cptr/options.py`** — keep. Becomes the
options pydantic model referenced by the config. The `stream_timeout_s`
field stays cptr-specific.

**`genesis_worker/services/cptr/install.py`** — DELETE
(replaced by `UvToolInstall`).

**`genesis_worker/services/cptr/lifecycle.py`** — DELETE
(replaced by `UvService` lifecycle).

**`genesis_worker/services/cptr/acquire.py`** — DELETE
(replaced by `UvService._post_install()`).

**`genesis_worker/services/cptr/ui/status.py`** — rewrite to use
`render_service_controls` + `render_tail_log` (the default shape).
Or keep the existing layout — it's already minimal.

### 1.5 Tests for phase 1

**`tests/test_docker_service_base.py`** — construction with a
minimal `DockerServiceConfig`, capability/method defaults, lifecycle
delegation to `DockerContainer` (mocked), install object identity,
auth methods when `AuthConfig` is present vs absent.

**`tests/test_uv_service_base.py`** — same shape. Plus
`_post_install()` hook (default no-op; subclass override runs).

**`tests/test_cptr_service.py`** — rewrite: cptr now builds from
`UvService` machinery. Asserts the post-install patch fires.
Drops the cptr-specific lifecycle/install tests because they
collapse into framework tests.

**Delete:** `tests/test_cptr_install.py`, `tests/test_cptr_lifecycle.py`.
Replace with assertions in `test_uv_service_base.py` and
`test_cptr_service.py`.

**Phase 1 gate:**

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

End of phase: `cptr` is ~50 lines instead of ~700 (counting all four
modules it used to span). Behavior unchanged from the user's
perspective.

---

## Phase 2 — YAML infrastructure

The spec, the loader, the hook/panel registries, the default UI,
the registry update. Built-in services still ship as Python plugins;
this phase lays the infrastructure and tests it.

### 2.1 Lifted utilities

**`genesis_worker/utils/seed_yaml_whitelist.py`** — moved verbatim
from `services/sillytavern/config.py`. Renamed entry point to
`seed_yaml_whitelist(target, *, key, extras, disable_docker_hosts)`.
Tailscale CGNAT moves to **`genesis_worker/utils/net/constants.py`**
(`TAILSCALE_CGNAT = "100.64.0.0/10"`). `seed_yaml_whitelist`
imports the constant; callers can too.

### 2.2 Pre-start hooks

**`genesis_worker/utils/services/hooks.py`** — registry of named
hook handlers.

```python
@dataclass(frozen=True)
class PreStartHookContext:
    service: InferenceService        # the service being started
    state_dir: Path
    data_dir: Path

HookHandler = Callable[[dict, PreStartHookContext], None]

_REGISTRY: dict[str, HookHandler] = {}

def register(kind: str) -> Callable[[HookHandler], HookHandler]: ...
def run(hooks: list[dict], ctx: PreStartHookContext) -> None: ...
```

Two handlers ship in v1:

- `seed_yaml_whitelist` — parses the `target` / `key` / `extras` /
  `disable_docker_hosts` fields and delegates to
  `utils.seed_yaml_whitelist.seed_yaml_whitelist`.
- `ensure_persistent_token` — parses `target` / `mode` /
  `generator`. Reads the file; if absent, calls
  `utils.ensure_persistent_file.ensure_persistent_file(target,
  mode=mode, generator=GENERATORS[generator])`.

**`genesis_worker/utils/ensure_persistent_file.py`** — new utility:

```python
def ensure_persistent_file(
    target: Path,
    *,
    mode: int = 0o600,
    generator: Callable[[], str] | None = None,
) -> str:
    """Return ``target`` contents; if absent, write ``generator()`` atomically."""
```

Generators (`random_hex_32`, `random_urlsafe_32`, …) live alongside
in the same file or in `hooks.py`.

### 2.3 UI panels

**`genesis_worker/utils/services/panels.py`** — registry of named
panel renderers.

```python
PanelRenderer = Callable[[InferenceService, dict], None]
_REGISTRY: dict[str, PanelRenderer] = {}

def register(kind: str) -> Callable[[PanelRenderer], PanelRenderer]: ...
def render(svc: InferenceService, panels: list[str], panel_config: dict) -> None: ...
```

Four panels ship in v1:

- `service_info` — display name + `render_service_controls` +
  `render_tail_log` is **not** here, see `log_tail`.
- `container_info` — image ref + container name + listen address +
  Web UI URL (reads `svc.image_ref`, `svc.container_name`, etc.).
- `auth_token` — calls `svc.auth_token()` and `svc.auth_enabled()`
  to render the JWT info or the copy-to-clipboard token. Mirrors
  the current crawl4ai HTML/JS component (re-implemented in
  `st.code` with a copy button, or `components.html` as today).
- `log_tail` — `render_tail_log(svc)`.

### 2.4 Default status page

**`genesis_worker/utils/services/default_status.py`** — single
framework file:

```python
def render_default_status(svc: InferenceService, *, url_slug: str) -> None:
    """The default status page for YAML-declared services."""
```

Renders the bordered service-info container, then iterates the
service's configured `ui_panels` and calls each registered
renderer. Reads `svc.ui_panels` (a property the base classes
populate from their config's `ui_panels` field; default
`("service_info", "container_info", "log_tail")` for docker,
`("service_info", "log_tail")` for uv).

The page is the `UiPage.path` for declarative services. The YAML
loader points `ui_pages` at this file with `url_path = f"{name}_status"`.

### 2.5 Spec pydantic models

**`genesis_worker/utils/services/spec.py`**:

```python
class OptionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str                                # "string", "int", "bool", "path", "list[string]", ...
    optional: bool = False
    default: Any = None

class DockerServiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    kind: Literal["docker"]
    name: str
    display_name: str
    description: str
    category: ServiceCategory
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate
    image: ImageSpec
    container: ContainerSpec
    env: dict[str, str] = {}
    volumes: dict[str, str] = {}
    pre_start_hooks: list[HookSpec] = []
    auth: AuthSpec | None = None
    ui: UiSpec = UiSpec()
    options: dict[str, OptionSpec] = {}

class UvServiceSpec(BaseModel):
    # same shape, kind="uv", different fields
    ...

ServiceSpecUnion = Annotated[DockerServiceSpec | UvServiceSpec, Field(discriminator="kind")]
```

`spec_to_options_model(options: dict[str, OptionSpec]) -> type[BaseModel]`
uses `pydantic.create_model()` to build the runtime options model
from the YAML's `options:` block. Supported types: `string`, `int`,
`float`, `bool`, `path`, `list[string]`, `list[int]`, `list[path]`.

### 2.6 YAML loader

**`genesis_worker/utils/services/loader.py`**:

```python
def load_service_spec(
    path: Path,
    *,
    context_factory: Callable[[str], ServiceContext],
) -> InferenceService:
    """Parse + validate + construct one YAML service."""
```

Steps:

1. `yaml.safe_load(path.read_text())`.
2. Validate against `ServiceSpecUnion` (dispatch on `kind`).
3. Build the runtime options pydantic model from
   `spec.options`.
4. Resolve path placeholders (`$state_dir`, `$data_dir`,
   `$vault_path`, `$options.X`, `$auth.token`) against the
   `ServiceContext`.
5. Construct `DockerServiceConfig` (or `UvServiceConfig`) from the
   resolved spec.
6. Instantiate `DockerService` (or `UvService`).

Path placeholder resolution is a small helper
`resolve_placeholders(value, ctx, options, auth_token)`. The same
helper handles string env values, volume targets, hook targets,
auth file paths — anywhere a YAML field references a context path
or an option.

### 2.7 Registry update

**`genesis_worker/registries.py`**:

- Add `_declarative_spec_paths(pkg_path: str) -> list[Path]` —
  `Path(pkg_path) / "_declarative"` globbed for `*.yaml`.
- `ServiceRegistry.__init__` calls it after the Python walker;
  duplicate `name` raises.
- The context factory for YAML services reuses
  `_common_kwargs(cls)` but with a synthetic `cls` (or refactors
  `_common_kwargs` to take the service name directly).

**`genesis_worker/tests/test_plugin_boundary.py`** — small
amendment: the empty `__init__.py` in `services/_declarative/`
walks and passes (no imports). YAML files don't walk. Comment at
the top of the test points at `test_declarative_specs.py` as the
parallel check for YAML.

### 2.8 Tests for phase 2

**`tests/test_yaml_loader.py`** — happy path for both kinds;
rejects `version: 2`; rejects unknown top-level keys; rejects
unknown option types; placeholder resolution for each supported
pattern.

**`tests/test_seed_yaml_whitelist.py`** — moved from
`tests/test_sillytavern_config.py`. Adjusted to import from
`utils.seed_yaml_whitelist` instead of `services.sillytavern.config`.

**`tests/test_ensure_persistent_file.py`** — generator runs when
file absent, returns existing contents when present, atomic write,
mode applied.

**`tests/test_default_status_page.py`** — Streamlit script-import
smoke: importing `utils.services.default_status` doesn't raise;
panel renderers register and call with a mock service.

**`tests/test_declarative_specs.py`** — placeholder for v1; the
built-in YAMLs don't exist yet, so this test currently walks an
empty dir. Phase 3 adds the YAMLs and the test asserts they
construct.

**Phase 2 gate:** same four commands as phase 1.

End of phase: a worker with no built-in YAML services still
behaves identically. The YAML infrastructure is exercised by
unit tests only.

---

## Phase 3 — Declarative conversions

Move crawl4ai and sillytavern off Python and onto YAML. Delete the
old modules. Consolidate tests.

### 3.1 `services/_declarative/` package marker

**`genesis_worker/services/_declarative/__init__.py`** — empty.
Marker so `importlib.resources.files("genesis_worker.services._declarative")`
resolves. (Alternatively, a non-Python directory + manual path
resolution. The package marker is the simpler option.)

### 3.2 crawl4ai.yaml

**`genesis_worker/services/_declarative/crawl4ai.yaml`** — as in
the ADR. Confirms the schema handles: docker kind, custom
`health_probe_path`, `web_ui_path`, `shm_size`, env vars with
placeholders, an `auth:` block, `pre_start_hooks:` with
`ensure_persistent_token`, `ui.status.panels:` with three named
panels, and an `options:` block with three typed fields.

### 3.3 sillytavern.yaml

**`genesis_worker/services/_declarative/sillytavern.yaml`** —
same shape, no `auth:` block, one `pre_start_hooks:` entry
(`seed_yaml_whitelist`).

### 3.4 Delete old Python

- `genesis_worker/services/crawl4ai/` — entire directory removed.
- `genesis_worker/services/sillytavern/` — entire directory
  removed (including `config.py` which is gone — the hook lives
  in `utils/services/hooks.py` now).

### 3.5 Test consolidation

**Delete:**
- `tests/test_crawl4ai_service.py`
- `tests/test_crawl4ai_options.py`
- `tests/test_crawl4ai_install.py`
- `tests/test_crawl4ai_lifecycle.py`
- `tests/test_crawl4ai_ui_imports.py`
- `tests/test_sillytavern_config.py` (replaced by
  `test_seed_yaml_whitelist.py`)

**Expand:** `tests/test_declarative_specs.py` — now loads the two
real YAMLs and asserts:

- Parse succeeds against `ServiceSpecUnion`.
- Construct succeeds with a fake `ServiceContext`.
- `svc.name`, `svc.display_name`, `svc.category`, `svc.description`
  match the YAML.
- `svc.is_available()` returns False (image not pulled) for a
  fresh context.
- `svc.capabilities()` matches the YAML.
- Pre-start hooks can be invoked against a stub context (no real
  docker call).
- The auth_token panel reads the configured file path.

### 3.6 Final gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
uv run pytest tests/test_declarative_specs.py -v
# manual smoke:
uv run streamlit run genesis_worker/ui/app.py
# navigate to /crawl4ai_status and /sillytavern_status; confirm
# the default template renders, the auth_token panel shows on
# crawl4ai, the seed_yaml_whitelist hook fires on sillytavern start.
```

End of phase: `services/crawl4ai/` and `services/sillytavern/` are
gone; their functionality lives in two ~40-line YAML files. Net
codebase reduction is in the hundreds of lines. New docker services
are a YAML drop-in.

---

## Phase ordering rationale

- Phase 1 is testable on its own: the base classes + cptr rewrite
  pass the gate without anything else changing.
- Phase 2 is testable on its own: the YAML infrastructure is
  exercised by unit tests against synthetic YAML strings; no
  built-in YAML needs to exist.
- Phase 3 is the only phase that removes user-visible Python
  modules. Doing it last means a regression in the YAML layer
  can be bisected to phase 2 or phase 3 cleanly.

If a regression surfaces mid-phase, we can stop after any phase
and ship what we have. The cptr rewrite in phase 1 is the first
proof; the YAML conversions in phase 3 are the dogfood.

## Files touched (summary)

**Added (24):**
- `genesis_worker/utils/services/{__init__,base,docker_service,uv_service,spec,loader,hooks,panels,default_status}.py`
- `genesis_worker/utils/seed_yaml_whitelist.py`
- `genesis_worker/utils/ensure_persistent_file.py`
- `genesis_worker/utils/net/constants.py`
- `genesis_worker/services/_declarative/{__init__.py,crawl4ai.yaml,sillytavern.yaml}`
- `genesis_worker/services/cptr/service.py` (rewrite)
- `genesis_worker/services/cptr/ui/status.py` (rewrite)
- `tests/test_docker_service_base.py`
- `tests/test_uv_service_base.py`
- `tests/test_yaml_loader.py`
- `tests/test_declarative_specs.py`
- `tests/test_seed_yaml_whitelist.py`
- `tests/test_ensure_persistent_file.py`
- `tests/test_default_status_page.py`

**Modified (3):**
- `genesis_worker/registries.py`
- `genesis_worker/services/cptr/options.py` (kept, becomes referenced by config)
- `genesis_worker/tests/test_plugin_boundary.py`

**Deleted (~12):**
- `genesis_worker/services/crawl4ai/` (whole dir)
- `genesis_worker/services/sillytavern/` (whole dir)
- `genesis_worker/services/cptr/{install,lifecycle,acquire}.py`
- `tests/test_crawl4ai_{service,options,install,lifecycle,ui_imports}.py`
- `tests/test_sillytavern_config.py`
- `tests/test_cptr_{install,lifecycle}.py`
