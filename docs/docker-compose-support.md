# Docker Compose Support for Multi-Container Services

**ADR-040** · Status: Proposed

## Context

The Genesis Worker currently deploys services as a **single Docker container** via `DockerContainer` (`docker run`). Every `kind: docker` declarative YAML produces one `DockerService` with one `DockerImageInstall`. This covers the majority of services (bifrost, crawl4ai, ntfy, etc.).

Some services require **multiple coordinated containers** — typically an application tier plus a database. ROMM (https://docs.romm.app/latest/getting-started/quick-start/) is the canonical example: it ships a `docker-compose.yml` with `romm` (web app) + `romm-db` (PostgreSQL). The motivation is to support those without requiring a Python plugin per service.

## Decision

Add a new `kind: compose` to the declarative YAML system. The YAML ships a **compose template** (a plain string populated by the standard `$placeholder` mechanism) that `docker compose` uses to orchestrate the full stack. The lifecycle maps to `docker compose up -d / down / ps` just as `kind: docker` maps to `docker run / stop / rm / inspect`.

The implementation follows the same layered pattern as `kind: docker`:

```
ComposeServiceSpec  (pydantic schema, spec.py)
      ↓
ComposeServiceConfig  (dataclass, compose_service.py)
      ↓
ComposeService  (DeclarativeServiceBase subclass, compose_service.py)
      ↓
DockerComposeStack  (utility, compose_stack.py)
```

Single-container `kind: docker` services are unaffected.

---

## Current Mechanism

### YAML → Service flow

1. `ServiceRegistry.__init__` calls `_declarative_spec_paths()` → lists `services/_declarative/*.yaml`
2. For each path: `load_service_spec(path, ctx)` → parses YAML → resolves `$placeholders` → validates `DockerServiceSpec | UvServiceSpec` → builds `DockerServiceConfig` → instantiates `DockerService(ctx, config=config)`
3. `DockerService.start()`:
   - Pre-start hooks fire
   - Auth token injected into env
   - `DockerContainer(container_name).run(image=..., ports=..., volumes=..., env=...)`
   - `docker run -d --name <name> --restart <policy> -p <host>:<port>:<container_port> -v ... -e ... <image>`
4. `DockerService.stop()`: `DockerContainer(name).stop()` → `docker stop --time 30 <name>`; `remove()` → `docker rm -f <name>`

### Install axis

`DockerImageInstall`: checks `docker image inspect <repo>:<tag>` → `InstallState.INSTALLED / NOT_INSTALLED`. Pull via `DockerPullAcquireSession` (`docker pull`).

---

## Proposed Design

### 1. Data Model

**`ComposeServiceSpec`** (spec.py, alongside `DockerServiceSpec` / `UvServiceSpec`):

```python
class ComposeServiceSpec(BaseModel):
    version: Literal[1]
    kind: Literal["compose"]
    name: str
    display_name: str
    description: str = ""
    category: ServiceCategory = ServiceCategory.OTHER
    capabilities: ServiceCapabilities
    resource_estimate: ServiceResourceEstimate

    # Compose-level config
    compose_template: str          # plain string, $placeholder-resolved at load time (the compose file body)
    compose_version: str = "3.8"  # compose file format version; emitted in the rendered output

    # Port mapping for the web UI. Optional at top level: when the spec
    # declares a ``listen_port`` option (the norm), the loader derives
    # the runtime port from the resolved option — single source of truth
    # for both the compose bind and the health probe.
    listen_host: str = "0.0.0.0"
    listen_port: int = 0
    health_probe_path: str = "/"

    # Env / volumes / args all come from the compose template
    # (no flat env:/volumes: top-level blocks like kind: docker)
    env: dict[str, Any] = Field(default_factory=dict)      # top-level compose env vars
    volumes: dict[str, Any] = Field(default_factory=dict)  # top-level compose volumes

    pre_start_hooks: list[HookSpec] = Field(default_factory=list)

    ui: UiSpec = Field(default_factory=UiSpec)
    options: dict[str, OptionSpec] = Field(default_factory=dict)

    data_dir_subpath: str | None = "data"
    log_filename: str | None = None
```

**`ComposeServiceConfig`** (compose_service.py, frozen dataclass):

```python
@dataclass(frozen=True)
class ComposeServiceConfig(DeclarativeServiceConfig):
    category: ServiceCategory
    compose_template: str
    compose_version: str
    listen_host: str
    listen_port: int
    health_probe_path: str
    extra_env: dict[str, str]
    extra_volumes: dict[str, str]
    pre_start_hooks: tuple[dict, ...]
    data_dir_subpath: str | None
```

**`DockerComposeStack`** (compose_stack.py):

```python
class DockerComposeStack:
    def __init__(self, project_dir: Path, project_name: str) -> None: ...
    def is_running(self) -> bool: ...
    def up(self, *, env: dict[str, str]) -> StartResult: ...
    def down(self) -> StopResult: ...
    def logs(self, tail_lines: int = 200) -> str: ...
```

- `project_dir` = `<state_dir>/compose/` (the directory where `docker compose` is invoked)
- `project_name` = `<service-name>-stack` (unique per service)
- `up()`: writes the rendered compose file to `project_dir/docker-compose.yml`, runs `docker compose up -d`
- `down()`: runs `docker compose down` in `project_dir`
- `is_running()`: runs `docker compose ps --format json` and checks for containers in `running` state
- `logs()`: runs `docker compose logs --tail N`

### 2. Compose Template Population

The `compose_template` in the YAML is a **plain string** using the standard `$placeholder` mechanism — the exact same `resolve_placeholders()` pass used for `kind: docker` and `kind: uv`. The loader substitutes every `$variable` (`$state_dir`, `$data_dir`, `$options.X`) against the runtime context *before* the template string is stored in `ComposeServiceConfig.compose_template`. At start time the stored string is written to disk verbatim — there is no second templating layer:

```python
# In ComposeService.start()
(project_dir / "docker-compose.yml").write_text(self.config.compose_template)
```

Deliberate design choice (resolved 16 Oct): a second templating engine (Jinja2) was briefly introduced for compose-specific interpolation, then removed. None of the compose templates need conditionals or loops, and a parallel template mechanism duplicated what `$placeholder` already does. If a future spec genuinely needs conditional compose output, that is when a real templating layer gets added — deliberately, not by accident.

### 3. Start / Stop / Status mapping

| `InferenceService` method | `ComposeService` implementation |
|---|---|
| `is_available()` | All images in the compose file are present locally (`docker image inspect` for each `image:` line) |
| `is_running()` | `stack.is_running()` — at least one container in `running` state |
| `start()` | 1. `_ensure_volume_dirs()` pre-creates bind-mount targets<br>2. Pre-start hooks fire<br>3. Write the resolved template → `docker-compose.yml`<br>4. `stack.up(env=merged_env)` |
| `stop()` | `stack.down()` |
| `status()` | `is_running()` → health probe → `ServiceState` |
| `wait_ready()` | `HealthProbe(...).wait_ready(timeout_s)` |
| `tail_log()` | `stack.logs(tail_lines)` |

### 4. `is_available()` for compose services

Unlike `DockerImageInstall` (single image), compose services may have multiple images. `is_available()` is true when **all** images referenced in the compose template are present locally. We parse the `image:` lines from the resolved template at construction time (or lazily at `is_available()` call) and check each via `DockerContainer.image_present()`.

`installs()` returns one `ComposeImageInstall` (subclass of `ServiceInstall`) that wraps all images. `ComposeImageInstall.install()` pulls each image in sequence. Future enhancement: parallel pull with progress streaming.

### 5. Backward Compatibility

- `kind: docker` / `kind: uv` are unchanged
- `DockerService`, `DockerServiceConfig`, `DockerServiceSpec` are untouched
- `ServiceRegistry` dispatches on `kind` in `_validate_spec()` — adding the `compose` branch is the only change
- `load_service_spec()` adds a third `elif kind == "compose"` branch that calls `_build_compose_config()` and returns `ComposeService(ctx, config=...)`
- Existing tests pass unchanged (compose is opt-in per YAML)

### 6. Migration / Risk

**Risk**: `docker compose` vs `docker-compose` (V1 standalone). The utility uses `docker compose` (built into Docker CE 23.0+, replaces the standalone `docker-compose` binary). This is already the modern standard. If the host has only `docker-compose` (old), the command fails — detectable at `is_running()` call with a clear error message.

**Risk**: compose project isolation. Each compose service uses a unique `project_name` (`<name>-stack`) and its own `project_dir` (`<state_dir>/compose/`). Two compose services never collide.

**Risk**: volume ownership. Same `_ensure_volume_dirs()` pre-creation as `DockerService` prevents root-owned bind-mount targets.

---

## ROMM as Test Service

ROMM (https://docs.romm.app/latest/getting-started/quick-start/) is a self-hosted ROM library manager. Official compose ships `romm` (Python/FastAPI app) + `romm-db` (PostgreSQL) + named volumes.

### `romm.yaml` structure

```yaml
version: 1
kind: compose
name: romm
display_name: ROMM
description: Self-hosted ROM library manager
category: media

capabilities:
  has_web_ui: true
  can_install: true
  can_serve_llm: false
  can_serve_image: false
  can_train_models: false
  can_generate_config: false
  can_export_for_agent: false

resource_estimate:
  vram_bytes_typical: 0
  vram_bytes_min: 0
  cpu_cores_recommended: 2

compose_version: "3.8"
health_probe_path: /

# No top-level listen_port — options.listen_port is the single source of
# truth; the loader derives the runtime port from the resolved option.

compose_template: |
  version: "3.8"
  services:
    romm:
      image: rommapp/romm:$options.romm_version
      container_name: romm
      restart: unless-stopped
      ports:
        - "$options.listen_host:$options.listen_port:8080"
      environment:
        ROMM_DB_HOST: romm-db
        ROMM_DB_NAME: $options.db_name
        ROMM_DB_USER: $options.db_user
        ROMM_DB_PASS: "$options.db_password"
      volumes:
        - $state_dir/library:/romm/library
        - $state_dir/config:/romm/config
      healthcheck:
        test: ["CMD", "wget", "--spider", "-q", "http://localhost:8080/api/health"]
        interval: 10s
        timeout: 5s
        retries: 5

    romm-db:
      image: postgres:16-alpine
      container_name: romm-db
      restart: unless-stopped
      environment:
        POSTGRES_DB: $options.db_name
        POSTGRES_USER: $options.db_user
        POSTGRES_PASSWORD: "$options.db_password"
      volumes:
        - $state_dir/db:/var/lib/postgresql/data

options:
  listen_host:
    type: string
    default: "0.0.0.0"
  listen_port:
    type: port
    default: 8080
  romm_version:
    type: string
    default: "latest"
  db_name:
    type: string
    default: "romm"
  db_user:
    type: string
    default: "romm"
  db_password:
    type: string
    default: "changeme"
    ui_label: "Database password"
```

Note: The `compose_template` field in the YAML is a **plain string** using `$placeholders` (like `$options.db_name`). The loader resolves every placeholder to its concrete value **before** the template is stored, so the stored string is the final compose file — written to disk verbatim at start time.

---

## File Changes

```
genesis_worker/utils/services/
  compose_stack.py       NEW — DockerComposeStack utility
  compose_service.py     NEW — ComposeService + ComposeServiceConfig

genesis_worker/utils/services/spec.py
  + ComposeServiceSpec pydantic model

genesis_worker/utils/services/loader.py
  + _build_compose_config()
  + elif kind == "compose" branch in load_service_spec()

genesis_worker/services/_declarative/
  romm.yaml              NEW — ROMM service definition

genesis_worker/tests/test_declarative_specs.py
  + "romm.yaml" in _BUILT_IN_SPECS
  + test_romm_identity
  + test_romm_render_includes_options_and_dirs
  + test_romm_is_available_reflects_images

genesis_worker/tests/test_services_registry.py
  + "romm" in expected service names

genesis_worker/tests/test_compose_service.py   NEW
genesis_worker/tests/test_compose_stack.py     NEW
```

---

## Open Questions

1. **`docker compose` availability check**: Should `is_available()` fail fast if `docker compose` isn't on PATH, or silently return `False`? (DockerContainer uses silent `False` for the same scenario.)
2. **Multiple images install**: `ComposeImageInstall.install()` pulls images sequentially. Should it stream combined progress output per image, or just fire-and-forget?
3. **`compose_version` in template vs spec**: The `compose_version` field in the spec is emitted as the top-level `version:` in the rendered file. Should we validate this is a known version string?
4. ~~**ROMM template variables**~~: **Resolved 16 Oct** — the Jinja2 pass was removed. The template is pure `$placeholder` output, resolved at load time. One template mechanism across the whole declarative system; a real templating layer returns only if a spec needs conditionals/loops.
