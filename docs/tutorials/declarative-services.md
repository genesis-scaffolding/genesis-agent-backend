# Adding a service declaratively

The Genesis Worker ships with a **declarative service template** (ADR-035) that
lets you add a new service by writing one YAML file under
`services/_declarative/`. No Python plugin, no subclass, no framework code to
write. The loader reads the YAML, resolves `$variable` substitutions against
the runtime context, and constructs the `InferenceService` for you.

For almost every docker-backed service we ship — bifrost, crawl4ai, sillytavern
— this is the right path. Python plugins still exist for the rare cases that
need them (cptr is the one example, because it monkey-patches the installed
binary's site-packages post-install). When in doubt, try YAML first.

## When to use YAML vs Python

Use YAML when the service is:

- A docker image with a standard HTTP/web UI
- Configured via env vars + bind mounts
- Has a health endpoint the framework can probe

Drop to Python (subclass `DockerService` or `UvService`) when you need:

- Custom post-install logic that runs before the binary starts
  (cptr patches `pi-agent`'s timeout — there's no YAML expression for that)
- A non-trivial installable that isn't a `docker pull` or `uv tool install`
- Service-specific lifecycle that doesn't fit the docker / uv split
- Dynamic discovery of versions (the built-in `DockerImageInstall` reads from
  the docker registry; if your image needs GitHub release tracking or a
  custom registry client, Python is the answer)

## Where it goes

```
genesis_worker/services/_declarative/
├── bifrost.yaml
├── crawl4ai.yaml
└── sillytavern.yaml
```

The filename **must equal the spec's `name:` field** — `bifrost.yaml` must
declare `name: bifrost`. The loader asserts this at construction time
(`path.stem != spec.name` is a hard error) so the "I renamed one but not the
other" mistake fails loud.

The directory itself must stay empty of Python — only `__init__.py` (which
must be empty, no imports) is allowed. `test_plugin_boundary.py` AST-walks
the directory and rejects any other modules. YAML files are invisible to the
plugin-boundary check, which is the point.

## The skeleton

Every declarative spec starts from this skeleton:

```yaml
version: 1                # schema version; 1 is the only value today
kind: docker              # docker | uv — see "Two backends" below
name: my-service          # must equal the filename without .yaml
display_name: My Service  # shown in the UI
description: Short blurb  # shown in the Service Catalog (~25-30 chars)
category: llm             # LLM | IMAGE | CHAT | CRAWLER | MEDIA | UTILITY | OTHER

capabilities:
  can_generate_config: false
  can_export_for_agent: false
  can_serve_llm: false
  can_serve_image: false
  can_train_models: false
  has_web_ui: true
  can_install: true

resource_estimate:
  vram_bytes_typical: 0
  vram_bytes_min: 0
  cpu_cores_recommended: 1

image:                     # docker-only; ignored for kind: uv
  repo: org/image-name
  tag: latest
  install_name: my-service
  source_url: https://hub.docker.com/r/org/image-name

container:                 # docker-only
  name: my-service         # container name as it appears in `docker ps`
  listen_host: 0.0.0.0
  listen_port: 8080
  internal_port: 8080      # optional; defaults to listen_port
  health_probe_path: /     # path the framework probes to confirm "RUNNING"
  web_ui_path: /           # appended to listen_address for the "Open Web UI" button
  restart_policy: unless-stopped
  shm_size: null           # optional; e.g. "1g" for browser-like containers
  puid_default: true       # inject PUID/PGID env vars so the container runs as the host user
  pgid_default: true

env: {}                    # environment variables (see "The $variable substitution")
volumes: {}                # bind mounts (host -> container)
pre_start_hooks: []        # optional; see "Pre-start hooks"
auth: null                 # optional; see "Auth (API tokens)"
ui:
  status_panels: []        # optional; see "UI panels"
options: {}                # optional user-editable settings (see "Options")
```

`version`, `kind`, `name`, `display_name`, `capabilities`, `resource_estimate`
are required. Everything else has sensible defaults.

## Two backends: `kind: docker` vs `kind: uv`

`docker` is the common case — a container image that the worker pulls and runs
behind `DockerContainer`. `uv` is for `uv tool install`-managed binaries that
run in a tmux session on the host (cptr is the only built-in user today).

The fields under `image:` and `container:` only apply to `kind: docker`.
For `kind: uv`, the equivalent fields are:

```yaml
kind: uv
package_name: pkg-name     # `uv tool install <this>`
binary_name: binary-name   # the binary's name on PATH
command: []                # args appended after the binary
install_env: {}            # env vars for the install session
command_env: {}            # env vars exported before the command runs
listen_host: 0.0.0.0
listen_port: 8080
health_probe_path: /
session_name: my-service   # tmux session name; defaults to `name`
```

Everything in this doc applies to `kind: docker`. The uv path is the same shape
but with different lifecycle internals.

## The `$variable` substitution

This is the part that makes the YAML powerful. Anywhere a string value can
appear in the spec, you can write a `$placeholder` and the loader substitutes
it before constructing the service. The placeholder is gone from the runtime
— by the time the framework boots the container, every `$variable` has been
replaced with a concrete value.

### Path placeholders (six total)

| Placeholder | Resolves to | Example (in a YAML) |
|---|---|---|
| `$state_dir` | `<xdg-state>/<service-name>` | `target: "$state_dir/api_token"` |
| `$data_dir` | `<xdg-data>/<service-name>` | `/app/data: "$data_dir/data"` |
| `$vault_path` | the user's model vault directory | (rarely used; for tools that read the catalog) |
| `$media_vault_path` | the user's media vault directory (default `~/media`) | `/photoprism/originals: "$media_vault_path"` |
| `$log_dir` | `<xdg-log>/<service-name>` | `LOG_PATH: "$log_dir/app.log"` |
| `$cache_dir` | `<xdg-cache>/<service-name>` | `CACHE_PATH: "$cache_dir"` |
| `$config_dir` | `<xdg-config>/<service-name>` | `CONFIG_FILE: "$config_dir/settings.json"` |

XDG base directories come from the framework's settings (`Settings.paths.*`)
which the registry populates per-service when it walks the YAMLs. None of these
paths are framework magic — they're the same paths the Python plugins get.

### Option placeholders (`$options.<name>`)

```yaml
options:
  log_level:
    type: string
    default: "info"
  jwt_enabled:
    type: bool
    default: false
  listen_port_override:
    type: int
    default: 8080

env:
  LOG_LEVEL: "$options.log_level"        # -> "info" at construction
  CRAWL4AI_JWT_ENABLED: "$options.jwt_enabled"  # -> bool False
  PORT: "$options.listen_port_override"  # -> int 8080
```

The `$options.<name>` placeholder resolves against the **merged options**:
defaults from the YAML's `options:` block, plus any overrides the user has
set via the framework's user-overrides env file. The merge happens once, at
construction time. By the time the container starts, the env var carries the
user's value (or the YAML default if they haven't customised).

**Type-preserving resolution.** This is the subtle part. The resolver returns
the option's *typed* value:

- `"$options.log_level"` (a YAML string placeholder, default `"info"`) →
  the string `"info"`.
- `"$options.jwt_enabled"` (default `false`) → the bool `False`. **Not** the
  string `"false"`.
- `"$options.listen_port_override"` (default `8080`) → the int `8080`.
  **Not** the string `"8080"`.

The single-placeholder case is what makes this work — the resolver sees
`"$options.jwt_enabled"`, fullmatches the `$options.X` pattern, looks up
the value, and returns it as-is. No string coercion.

**Mixed strings return strings.** If the placeholder is mixed with literal
text, the resolver falls back to regex substitution and you get a string:

- `LOG_PATH: "$state_dir/$options.subpath"` → string
  `"/.../state/<service-name>/<subpath>"` (the subpath is `str()`-ed inside
  the regex).

If you need a typed value, make the whole string be one placeholder.

### Auth placeholders

The `auth:` block supports `$options.X` in `enabled_option` and
`fallback_option`:

```yaml
auth:
  enabled_option: "$options.jwt_enabled"  # resolves to bool at construction
  fallback_option: "$options.api_token"   # resolves to str | None
  token_env_var: CRAWL4AI_API_TOKEN
  token_file: "$state_dir/api_token"
  token_file_mode: "0o600"
  token_generator: random_hex_32
```

The loader resolves `enabled_option` to the option's typed bool value and
stores it on `AuthConfig.enabled` as `bool | None`. Runtime code reads
`self.config.auth.enabled` directly — no `getattr` lookup, no string
reference, no "is this the right option name" surprises.

### Where placeholders can appear

| Field | Placeholders supported? |
|---|---|
| `env.*` values | Yes |
| `volumes.*` values (host path) | Yes |
| `auth.token_file` | Yes |
| `auth.enabled_option`, `auth.fallback_option` | Yes (`$options.X` only) |
| `pre_start_hooks[].target` | Yes (resolved before the hook fires) |
| `image.repo`, `image.tag` | No — these are concrete image references |
| `container.listen_host`, `listen_port`, `internal_port`, `public_host` | Yes (`$options.X` only) — user-editable since ADR-036 |

## Options block

User-editable settings live in `options:`. Each entry declares:

```yaml
options:
  <name>:
    type: <string|int|float|bool|path|port|env_map|mount_map|list[string]|list[int]|list[path]>
    optional: <true|false>     # adds | None to the type
    default: <value>            # used when no user override
    ui_label: "Display name"    # shown in the configure panel (optional)
    ui_help: "Tooltip text"     # shown next to the widget (optional)
    ui_group: "Network"         # section heading in the form (optional)
```

The type system is intentionally closed — exotic shapes (nested models,
custom validators) require a Python `options.py` companion file. For typical
env-var-style configuration the YAML DSL is enough.

Three new types landed in ADR-036:

- **`port`** — `int` constrained to `(0, 65536)`. Use for `listen_port`,
  `internal_port`, and any other service-controlled port.
- **`env_map`** — `dict[str, str]`. A free-form map of additional env
  vars. The user populates key/value pairs through the configure panel;
  YAML-declared typed knobs in `env:` still win on collision (the typed
  knob is the discovery surface; the map is the long tail).
- **`mount_map`** — `dict[str, Path]`. Same shape for bind mounts
  (container path → host path).

`optional: true` means "may be unset by the user". The Python type becomes
`<declared> | None`, and pydantic accepts `None` for the value.

User overrides flow in via the framework's user-overrides env file, which the
registry merges into `ctx.options` before the loader runs. So:

1. User sets `my_service.log_level: "debug"` in user overrides
2. Registry builds `ctx.options = {"log_level": "debug", ...}`
3. Loader does `options_model(**ctx.options)` → typed instance with overrides
4. `$options.log_level` resolves to `"debug"`

Map options don't fit the flat KEY=VALUE dotenv format. They live in a
per-service JSON sidecar at
`<config_dir>/services/<name>.overrides.json`. The Settings page's flat-key
override path stays as-is for backward compat with bifrost / crawl4ai /
sillytavern; the sidecar carries map values only.

This is why the user doesn't have to restart the worker to pick up changes
to **most** fields — but for `env` and `volumes` whose `$...` substitution
happens at construction, the user DOES need to restart the service on its
own page (the resolved values are baked into `DockerServiceConfig.extra_env`
and never re-resolved at start time). The configure panel's **Apply**
button rebuilds the in-memory service; **Apply & restart** stops + starts
the container so the new config takes effect immediately (ADR-036).

## Pre-start hooks

Hooks fire after image-pull and before container start, in YAML-declared
order. The framework dispatches each entry by `kind` to a registered handler
in `genesis_worker/utils/services/hooks.py`. Three handlers ship today:

```yaml
pre_start_hooks:
  - kind: ensure_persistent_token
    target: "$state_dir/api_token"
    mode: "0o600"
    generator: random_hex_32

  - kind: seed_yaml_whitelist
    target: "$data_dir/config/config.yaml"
    key: whitelist
    extras: [100.64.0.0/10]
    disable_docker_hosts: true

  - kind: materialize_orchestrator_config
    target: "$data_dir/data/config.json"
    # format: json   # default; 'yaml' also supported
```

`ensure_persistent_token` reads-or-creates a token file. Idempotent.
`seed_yaml_whitelist` writes a YAML whitelist key with loopback + docker
bridge gateway + host LAN subnets + Tailscale CGNAT + user entries. Also
idempotent — if the file already has a correct whitelist, it's a no-op.

`materialize_orchestrator_config` (ADR-038) writes the
`config` body the orchestrator POSTed to
`/v1/services/{name}/start` (or `/restart`) into the declared target
before the container boots. When no body is passed the hook is a no-op
and the service starts with its on-disk config (today's behaviour). The
freshly-started container reads the new file on launch. Bind-mount the
target into the container (e.g. `volumes: { /app/data: "$data_dir/data" }`)
so the service sees the freshly-written config.

The hook target is `$variable`-resolved at construction time, so the runtime
handler just reads an absolute path string. No `ctx.data_dir` /
`ctx.state_dir` lookups at hook time.

**Adding a new hook kind** is one function in `utils/services/hooks.py`:

```python
@register("my_new_hook")
def _my_new_hook(entry: dict, ctx: PreStartHookContext) -> None:
    target = Path(entry["target"])  # already resolved
    # ... do the work ...
```

No spec change, no loader change, no YAML schema migration. The hook entry's
field shape is whatever you want — handlers parse their own fields from the
dict.

## Auth (API tokens)

If the container expects a static API token (crawl4ai's `CRAWL4AI_API_TOKEN`
is the canonical example), declare `auth:`:

```yaml
auth:
  enabled_option: "$options.jwt_enabled"   # if true, JWT mode (no token needed)
  fallback_option: "$options.api_token"    # user-typed override beats the file
  token_env_var: CRAWL4AI_API_TOKEN
  token_file: "$state_dir/api_token"
  token_file_mode: "0o600"
  token_generator: random_hex_32
```

What this wires up:

1. **Token file persistence.** Declare an `ensure_persistent_token` hook with
   the same `target` as `auth.token_file` so the file exists at start time.
   The hook creates it (mode 0o600) if missing, leaves it alone if present.
2. **JWT mode bypass.** When `jwt_enabled` is true, the container is told
   `<token_env_var>_JWT_ENABLED=true` and no token is injected.
3. **Env injection.** When JWT mode is off, the container gets
   `<token_env_var>=<token>` where the token comes from the user-typed
   option, the on-disk file, or auto-generated 256-bit hex.

Don't try to express the token in the `env:` block — `start()` overwrites
the env var at runtime with the resolved token. The `auth:` block is the
source of truth.

## UI panels

The status page for a docker service renders, in order:

1. **`service_info`** — mandatory. Carries install / start / stop buttons.
2. **`container_info`** — docker-specific. Shows container state, image, ports.
3. **`configure`** — auto-generated **dialog** for the service's
   user-editable options. The status page renders a single
   `Configure` button; clicking it opens a modal (`@st.dialog`)
   with one widget per option in `svc.config.option_specs`,
   grouped by `ui_group`. The form is intentionally modal so the
   status page stays focused on operational info (install /
   start / stop, container state, logs) and the dense form
   doesn't dump inline (ADR-036).

   **Apply** persists scalars to `user-overrides.env` and map types
   to the JSON sidecar, then rebuilds the in-memory service.
   **Apply & restart** does that and stops + starts the container so
   the new config takes effect immediately.

   `configure` is **auto-included** for any service that declares an
   `options:` block — YAML authors don't need to add `- configure` to
   `ui.status_panels`. Services without `options:` (Python services
   that ship their own `options.py`, services with no user-tunable
   settings) don't get the panel.
4. **`log_tail`** — last N bytes of the log file.

If the YAML declares extra panels, they append after the defaults:

```yaml
ui:
  status_panels:
    - auth_token   # shown when the service has an `auth:` block
```

`auth_token` renders the current token (with copy button) when JWT mode is
off, or a "JWT enabled" badge when it's on. The framework registers
`auth_token` as a panel kind, so this Just Works if your service has
`auth:` declared.

## Categories

The dashboard groups services by `category`. Pick the closest fit:

| Category | Use for |
|---|---|
| `llm` | Anything that serves LLM requests (llama-swap, OpenAI-compatible gateways like bifrost) |
| `image` | Image generation backends (comfyui) |
| `chat` | Chat frontends (sillytavern) |
| `crawler` | Web crawlers / scrapers (crawl4ai) |
| `media` | Media processing pipelines |
| `utility` | General-purpose tools that don't fit elsewhere |
| `other` | Temporary — the dashboard renders these under a less prominent heading as a nudge to update |

`OTHER` is a stopgap. If your service has a clear category, declare it.

## Worked example: bifrost from scratch

We want to add Bifrost (`maximhq/bifrost`), an LLM gateway proxy. Steps:

1. **Pick a port.** llama-swap owns 8080. Use 9090.
2. **Read the upstream docs.** Note: bind mount `-v $(pwd)/data:/app/data`,
   env vars `APP_HOST`, `APP_PORT`, `LOG_LEVEL`, `LOG_STYLE`. Web UI at `/`.
3. **Identify user-editable options.** `LOG_LEVEL` and `LOG_STYLE` — both
   should be user-editable.
4. **Write the YAML:**

```yaml
# bifrost.yaml
version: 1
kind: docker
name: bifrost
display_name: Bifrost
description: LLM gateway proxy with OpenAI-compatible API
category: llm

capabilities:
  can_generate_config: false
  can_export_for_agent: false
  can_serve_llm: true
  can_serve_image: false
  can_train_models: false
  has_web_ui: true
  can_install: true

resource_estimate:
  vram_bytes_typical: 0
  vram_bytes_min: 0
  cpu_cores_recommended: 1

image:
  repo: maximhq/bifrost
  tag: latest
  install_name: bifrost
  source_url: https://hub.docker.com/r/maximhq/bifrost

container:
  name: bifrost
  listen_host: 0.0.0.0
  listen_port: 9090
  internal_port: 9090
  health_probe_path: /
  web_ui_path: /

env:
  APP_HOST: "0.0.0.0"
  APP_PORT: "9090"
  LOG_LEVEL: "$options.log_level"
  LOG_STYLE: "$options.log_style"

volumes:
  /app/data: "$data_dir/data"

options:
  log_level:
    type: string
    default: "info"
  log_style:
    type: string
    default: "json"
```

5. **Bootstrap-test** before merging — load the spec against a hermetic
   context and confirm it parses, validates, and constructs:

```python
from pathlib import Path
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import load_service_spec

ctx = service_ctx(Path("/tmp"), name="bifrost")
svc = load_service_spec(Path("genesis_worker/services/_declarative/bifrost.yaml"), ctx=ctx)
assert svc.name == "bifrost"
assert svc.config.listen_port == 9090
assert svc.image_ref == "maximhq/bifrost:latest"
```

6. **Add the spec to the test suite.** `tests/test_declarative_specs.py`
   lists built-in specs in `_BUILT_IN_SPECS`. Add the new YAML to the tuple
   so the parametrised tests pick it up. Add a spec-specific identity test
   (catches "the loader changed and broke my YAML" silently).
7. **Update the registry-all test.** `tests/test_services_registry.py`
   enumerates every service the registry constructs. Add the new name to
   the expected set.
8. **Run all four gates:** `uv run pytest -q`, `uv run pyright`,
   `uv run ruff check genesis_worker`, `uv run ruff format --check genesis_worker`.

## Worked example: photoprism with user-editable config

PhotoPrism is the canonical example of a service that benefits from
ADR-036 — it has a bind mount pointing at a per-deployment photos
directory, an admin password to set, several env vars beyond the
common ones, and a port the operator might want to move. All of
this lands in the YAML via the `options:` block, surfaced through
the `configure` panel, and persisted through the same precedence
chain as everything else (ADR-036).

```yaml
# photoprism.yaml
version: 1
kind: docker
name: photoprism
display_name: PhotoPrism
description: Photo and video organizer
category: media

capabilities:
  has_web_ui: true
  can_install: true
resource_estimate:
  vram_bytes_typical: 0
  vram_bytes_min: 0
  cpu_cores_recommended: 2

image:
  repo: photoprism/photoprism
  tag: latest
  install_name: photoprism
  source_url: https://hub.docker.com/r/photoprism/photoprism

container:
  name: photoprism
  listen_host: 0.0.0.0
  listen_port: $options.listen_port       # user-editable
  internal_port: 2342
  health_probe_path: /
  web_ui_path: /
  restart_policy: unless-stopped
  security_opts: [seccomp=unconfined, apparmor=unconfined]

env:
  PHOTOPRISM_UPLOAD_NSFW: "$options.upload_nsfw"
  PHOTOPRISM_ADMIN_PASSWORD: "$options.admin_password"

volumes:
  /photoprism/storage: "$data_dir/storage"
  /photoprism/originals: "$options.pictures_dir"   # user-editable

# No ``ui.status_panels`` entry needed; ``configure`` auto-includes
# for any service with options.

options:
  listen_port:
    type: port
    default: 2342
    ui_label: Web UI port
    ui_help: Host-side port the web UI listens on
    ui_group: Network
  pictures_dir:
    type: path
    default: "$media_vault_path"
    ui_label: Pictures directory
    ui_help: Directory PhotoPrism indexes for photos and videos (defaults to the worker's media vault at ~/media)
    ui_group: Storage
  admin_password:
    type: string
    optional: true
    ui_label: Admin password
    ui_help: Blank keeps the auto-generated one
    ui_group: Security
  upload_nsfw:
    type: bool
    default: true
    ui_label: Allow NSFW uploads
    ui_help: Toggle the PHOTOPRISM_UPLOAD_NSFW env var
    ui_group: Security
  extra_env:
    type: env_map
    ui_label: Extra environment variables
    ui_help: Additional PhotoPrism env vars (upstream docs)
    ui_group: Advanced
  extra_mounts:
    type: mount_map
    ui_label: Extra volume mounts
    ui_help: Additional host→container bind mounts
    ui_group: Advanced
```

How the user interacts with this:

1. They open the PhotoPrism status page and click the **Configure**
   button. A modal dialog opens with one widget per option,
   grouped by `ui_group` (Network / Storage / Security / Advanced).
2. They edit `pictures_dir` to `/srv/photos` and click **Apply**.
3. The dialog writes `GENESIS_SERVICES__PHOTOPRISM__PICTURES_DIR=/srv/photos`
   to `user-overrides.env` and rebuilds the in-memory service. The
   dialog closes; the running container still uses the old bind
   mount until restart.
4. They open the dialog again and click **Apply & restart**, or
   stop/start the service manually on its status page. The
   container starts with the new bind mount.

The two map options (`extra_env`, `extra_mounts`) use a key/value row
editor instead of scalar fields. Edits land in
`<config_dir>/services/photoprism.overrides.json`. The merge order
inside `Settings.options_for` is scalars-first, maps-second, so the
sidecar wins on key collisions (the typed knob is the discovery
surface; the map is the long tail — a user who types
`PHOTOPRISM_UPLOAD_NSFW=false` into `extra_env` while the typed knob
is `true` gets the long-tail value).

## Limitations (when to fall back to Python)

YAML can't express:

- **Post-install file patches** (cptr's `patch_pi_timeout`). The hooks
  system gives you a registry of pre-start actions, but they're declarative
  too — if you need Python to manipulate a file, write a Python plugin.
- **Custom installables.** The built-in installable types are
  `DockerImageInstall` (docker pull + 15-min registry cache) and
  `UvToolInstall` (uv tool install + PyPI lookup). Anything else needs
  Python.
- **Multi-process services** that need orchestration across several
  containers / processes. Declarative services are one container or one
  binary.
- **Dynamic port discovery** — `listen_port` is a fixed integer. If the
  service picks its own port and reports it via API, you need Python.
- **Lifecycle that doesn't fit start/stop/status** — anything requiring a
  third state (e.g. "migrating") or a custom restart sequence.

If your service hits any of these, drop into Python — but write the YAML
*first* to clarify what the framework already gives you, then add only the
minimum Python the YAML can't express.
