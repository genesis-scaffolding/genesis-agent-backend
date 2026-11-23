# Adding a multi-container (docker compose) service declaratively

When the service you want to ship is **more than one container** — an app plus
its database, a proxy in front of a worker — the declarative path still
applies, but with `kind: compose` instead of `kind: docker`. You write one YAML
file under `services/_declarative/`, declare every image the stack uses, and
write the `docker-compose.yml` body once as a plain string. No Python plugin.

The single-container tutorial is
[`declarative-services.md`](declarative-services.md) — the options block, the
`$variable` substitution engine, the port conventions, and the test gates
described there all apply here unchanged. This tutorial covers only what is
different: the `images:` block, the compose template, and the two-phase
placeholder resolution that compose introduces.

## When to use `kind: compose`

- The upstream project ships (or effectively requires) a
  `docker-compose.yml` — app + database, app + queue, etc.
- You need `depends_on`, service-internal networking, or multiple healthchecks.

If the service is one container with env vars and bind mounts, use
`kind: docker` — it's simpler and the loader does more of the work for you.

## Anatomy of a compose spec

```yaml
version: 1
kind: compose                       # <- the only structural difference
name: romm                          # must equal the filename without .yaml
display_name: ROMM
description: Self-hosted ROM library manager
category: media

capabilities:
  has_web_ui: true
  can_install: true
  # ... other capability flags ...

resource_estimate:
  vram_bytes_typical: 0
  vram_bytes_min: 0
  cpu_cores_recommended: 2

# --- compose-specific fields -------------------------------------------

# Every image the stack uses. If it's in the template, it's declared here.
images:
  - name: romm
    repo: rommapp/romm
    default_tag: latest
  - name: romm-db
    repo: postgres
    default_tag: 16-alpine

health_probe_path: /

# The compose file body as a plain string. See "The template" below.
compose_template: |
  version: "3.8"
  services:
    romm:
      image: $images.romm
      ...

# User-tweakable options — identical mechanics to kind: docker.
options:
  listen_host:
    type: string
    default: "0.0.0.0"
    ui_label: Listen host
    ui_group: Network
  listen_port:
    type: port
    default: 8101
    ui_label: Web UI port
    ui_group: Network
  roms_dir:
    type: path
    default: "$media_vault_path/roms"
    ui_label: ROMs directory
    ui_group: Storage
```

### The `images:` block

Image identity is **framework-level, not a user option**. Every image the
stack uses gets a `ComposeImage` entry:

- `name` — the stable identifier the template references via
  `$images.<name>`. Hyphens are allowed (`romm-db`).
- `repo` — the pull reference (`rommapp/romm` for Docker Hub,
  `ghcr.io/org/img` for GHCR).
- `default_tag` — used when the user has not picked a version.

Each entry becomes a real `DockerImageInstall` at construction: real registry
tag discovery, a version picker in the GUI, and a recorded selection file.
That is what makes "the user picks `rommapp/romm:2.9.1` in the configure
panel" a first-class operation instead of a template edit.

**Rule: if an image appears in the template, it must be declared here.**
The loader enforces it: a template `image:` line that is not
`$images.<declared name>` is rejected at load time, with the offending
line named. (A `build:` target is unaffected — there is no image to pull
and no version to pick.) The rule exists because the two halves of the
service read different lists: the **availability check** reads the
images of the rendered template, while the **install axis** is built from
the *declared* ones. An undeclared image would therefore be required by
the service and fetchable by no install button — the service could never
become available from the UI, silently, forever.

Note that `start()`'s `docker compose pull` *does* fetch every image in
the file, declared or not — that is why the stack can be brought up by
hand. What an undeclared image breaks is the install axis: the UI cannot
fetch it, so the service never reaches the "available" state the Start
button depends on.

### The `credential_env:` block

Some services read a credential from an environment variable whose *name*
depends on which provider the user picked (a DNS provider, a cloud
account). The `credential_env:` block says which of the spec's options
selects the name, and which value maps to which variable:

```yaml
options:
  dns_provider:
    type: string
    default: "godaddyv3"
  dns_api_token:
    type: string
    default: ""

credential_env:
  provider_option: dns_provider
  by_provider:
    godaddyv3: GODADDYV3_PERSONAL_ACCESS_TOKEN
    cloudflare: CLOUDFLARE_DNS_API_TOKEN
```

The template then writes the credential under the selected provider's own
variable:

```yaml
compose_template: |
  ...
      environment:
        $credential_env: "$options.dns_api_token"
```

`provider_option` must name one of the spec's own options — a typo there
is rejected at load time, like any other spec error. A *value* the map
does not cover is a runtime value: the service still loads, and the
render refuses to write the file, naming the value and the known ones.
The mechanism is compose-only (the docker kind substitutes environment
values, not environment keys) — see
[the declarative-services tutorial](declarative-services.md#credential-env-placeholders-credential_env).

### Installing a multi-image stack

The Status page's **Install** button installs *the service* — every image
the stack needs, not just the first one (ADR-049). For a two-image stack
like ROMM that means two pulls; for paperless-ngx, three; for immich,
four. Before the install the page names what is missing:

> Still needed: postgres:18-alpine, valkey/valkey:9-alpine — Install
> fetches all of them.

The install is a single session: the images are pulled in order, each
one at its recorded version when the operator has pinned one on the
Images page, and the ones already on disk are skipped (so a partial
install resumes). The outcome — success or failure — stays on the page
until it is dismissed, and a failure names the image that failed and the
reason.

The **Images page** keeps its own job: one section per image, where the
operator picks a tag, installs, updates or uninstalls a single image.

### The template

`compose_template` is the entire `docker-compose.yml` body, written as a YAML
block scalar. It is populated by the **standard `$placeholder` mechanism** —
there is no second templating language, no `{{ }}`. The same markers you know
from single-container specs work inside the template string:

- `$state_dir`, `$data_dir`, `$vault_path`, `$media_vault_path`, `$log_dir`,
  `$cache_dir`, `$config_dir` — the context paths
- `$options.<name>` — every option in the `options:` block
- `$images.<name>` — the image markers
- `$credential_env` — the environment variable a credential is written under

`$images.<name>` and `$credential_env` are the two compose-specific
markers: both survive the load-time pass untouched and are materialised
at render time.

### Single source of truth for the port

Compose specs have no `container:` block. Declare a `listen_port` **option**
and bind it in the template (`"$options.listen_host:$options.listen_port:8080"`);
the loader derives the runtime port (health probe, web UI endpoint) from the
*resolved option*, so the probe and the container can never desync — including
when the user overrides the port in the configure panel. Top-level
`listen_port` on the spec is a fallback for services without a port option.

The option may also carry a `$auto_port.<name>` marker, in which case the
framework allocates the host port at start time and the template publishes
the allocation (ADR-043 — see
[the declarative-services tutorial](declarative-services.md#auto-port-placeholders-auto_portname)).
The loader rejects a compose spec whose runtime port is a marker the
template does not publish: the health probe would watch a port the stack
never binds.

## Two-phase placeholder resolution

This is the one genuinely new idea in compose specs, and it's worth
understanding before writing a template.

**Phase 1 — load time** (same as every other declarative service). When
`load_service_spec` runs, *every* placeholder in the template is resolved
against the service context and the merged options. After loading, the stored
template contains concrete paths, the user's actual option values, and two
kinds of marker left: `$images.<name>` and `$credential_env`. The image
markers are deliberately left intact, because the thing they resolve to — the
user's recorded version selection — is not known at load time; it can be
changed in the GUI at any point without re-loading the spec. The credential
marker survives phase 1 the same way: the environment variable's name is
derived from an option's value, and phase 2 is where that value is read.

**Phase 2 — render time.** On every `start()`, the service builds the
effective refs (`recorded selection, else default_tag`) and runs the *same*
`resolve_placeholders` engine over the stored template with an
`images={name: "repo:tag"}` table and a `credential_env=<VARIABLE_NAME>`
source — the name the `credential_env:` block maps the current option value
to. The result is the final `docker-compose.yml`, written to
`<state_dir>/compose/docker-compose.yml` verbatim.

The consequence for you as an author: **a user's GUI version pick reaches the
running stack on the next start, with no code change and no spec reload.**
That's the whole reason phase 2 exists.

Everything else in the template is final after load — the two marker kinds
above are the exception. If you write a marker that resolves to nothing (a
typo, an option you forgot to declare), it survives to the rendered file as
a literal `$typo` string — the engine never silently drops markers. Catch it
in the bootstrap test below, not at 2am.

The credential marker is the exception to *that*: an unmapped provider (or a
template that uses `$credential_env` without a `credential_env:` block) makes
the render refuse to write the file, rather than ship a credential under a
name nobody reads.

## Options: user-tweakable knobs in the template

The mechanics are identical to `kind: docker` — see
[`declarative-services.md` → Options block](declarative-services.md#options-block).
The compose-specific part is simply *where the substituted values land*:
inside the template string.

```yaml
options:
  db_password:
    type: string
    default: "changeme"
    ui_help: Change this to a strong unique password before starting
    ui_group: Database
  roms_dir:
    type: path
    default: "$media_vault_path/roms"
    ui_group: Storage

compose_template: |
  ...
      environment:
        DB_PASSWD: "$options.db_password"   # -> the user's value at load
        DB_NAME: $options.db_name            # -> "romm" (or the override)
      volumes:
        - $options.roms_dir:/romm/library    # -> /home/<user>/media/roms:/romm/library
```

Notes that matter when writing compose templates:

- **Option defaults can be markers.** `roms_dir`'s default
  `"$media_vault_path/roms"` is resolved before it's used as a lookup value,
  so `$options.roms_dir` in the template resolves to the concrete path —
  never to another marker.
- **The template is a string, so everything lands as a string.** A lone
  `$options.some_bool` in the template gets stringified during substitution.
  For env values, quote booleans in the compose YAML
  (`ROMM_FRONTEND: "1"`) so compose parses them as you intend.
- **`env_map` / `mount_map` options** (the free-form long tail) are
  docker-kind features; compose services declare their env and volumes
  directly in the template.

## Worked example: ROMM

ROMM (`romm.yaml`, shipped) is a two-container stack: the web app and a
PostgreSQL database. The full file:

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

resource_estimate:
  vram_bytes_typical: 0
  vram_bytes_min: 0
  cpu_cores_recommended: 2

compose_version: "3.8"
health_probe_path: /

# Port 8101 — 8080 is taken by llama-swap on this host.

images:
  - name: romm
    repo: rommapp/romm
    default_tag: latest
  - name: romm-db
    repo: postgres
    default_tag: 16-alpine

compose_template: |
  version: "3.8"
  services:
    romm:
      image: $images.romm
      container_name: romm
      restart: unless-stopped
      ports:
        - "$options.listen_host:$options.listen_port:8080"
      environment:
        ROMM_DB_DRIVER: postgresql
        DB_HOST: romm-db
        DB_PORT: "5432"
        DB_USER: $options.db_user
        DB_PASSWD: "$options.db_password"
        DB_NAME: $options.db_name
        ROMM_FRONTEND: "1"
      volumes:
        - $options.roms_dir:/romm/library
        - $state_dir/config:/romm/config
      healthcheck:
        test: ["CMD", "wget", "--spider", "-q", "http://localhost:8080/health"]
        interval: 10s
        timeout: 5s
        retries: 5
        start_period: 30s

    romm-db:
      image: $images.romm-db
      container_name: romm-db
      restart: unless-stopped
      environment:
        POSTGRES_DB: $options.db_name
        POSTGRES_USER: $options.db_user
        POSTGRES_PASSWORD: "$options.db_password"
      volumes:
        - $state_dir/db:/var/lib/postgresql/data
      healthcheck:
        test: ["CMD", "pg_isready", "-U", "$options.db_user"]
        interval: 10s
        timeout: 5s
        retries: 5

options:
  listen_host:
    type: string
    default: "0.0.0.0"
    ui_label: Listen host
    ui_group: Network
  listen_port:
    type: port
    default: 8101
    ui_label: Web UI port
    ui_group: Network
  roms_dir:
    type: path
    default: "$media_vault_path/roms"
    ui_label: ROMs directory
    ui_group: Storage
  db_name:
    type: string
    default: "romm"
    ui_label: Database name
    ui_group: Database
  db_user:
    type: string
    default: "romm"
    ui_label: Database user
    ui_group: Database
  db_password:
    type: string
    default: "changeme"
    ui_label: Database password
    ui_help: Change this to a strong unique password before starting
    ui_group: Database
```

Note the storage model: `$options.roms_dir` mounts at `/romm/library`,
ROMM's *library* location, not its scan source. ROMM has two storage
concepts — the library (where UI uploads and managed files live) and the
scanned directories (an optional read-mostly source for Import). Mounting
the media vault at the library makes the vault the single source of truth:
uploads and scan results both land in `~/media/roms`. The earlier
`$state_dir/library` + `$options.roms_dir:/romm/roms` split put uploads in
XDG state and left the vault empty — confusing, and it fought the vault-as-
source-of-truth principle.

Trace one line through both phases:

- `"$options.listen_host:$options.listen_port:8080"` → load →
  `"0.0.0.0:8101:8080"` (concrete in the stored template) → render →
  unchanged.
- `image: $images.romm-db` → load → unchanged (deliberate) → render →
  `image: postgres:16-alpine`, or `postgres:<picked-tag>` if the user
  installed a specific version from the GUI.

## Bootstrap-test before merging

Same pattern as the single-container tutorial, plus render-phase checks:

```python
from pathlib import Path
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import load_service_spec

ctx = service_ctx(Path("/tmp"), name="romm")
svc = load_service_spec(Path("genesis_worker/services/_declarative/romm.yaml"), ctx=ctx)

# Load phase: concrete paths and options, image markers still present.
t = svc.config.compose_template
assert "$state_dir" not in t and "$options.listen_port" not in t
assert "$images.romm" in t and "$images.romm-db" in t
assert "0.0.0.0:8101:8080" in t

# Render phase: every marker gone, effective refs present.
rendered = svc._rendered_template()
assert "$images." not in rendered
assert "$credential_env" not in rendered
assert "$options." not in rendered
assert "$state_dir" not in rendered
assert "image: rommapp/romm:latest" in rendered
assert "image: postgres:16-alpine" in rendered
assert "/tmp/media-vault/roms:/romm/library" in rendered  # vault is the store

# The install axis covers the whole stack (ADR-049) — the Status page's
# Install button fetches every one of them.
assert [i.name for i in svc.installs()] == ["romm:romm", "romm:romm-db"]

# Port override flows through to both the template and the runtime config.
ctx2 = service_ctx(Path("/tmp"), name="romm", options={"listen_port": 9090})
svc2 = load_service_spec(Path("genesis_worker/services/_declarative/romm.yaml"), ctx=ctx2)
assert svc2.config.listen_port == 9090
assert "0.0.0.0:9090:8080" in svc2.config.compose_template
```

The `assert "$images." not in rendered`-style checks are your guard against the
surviving-marker failure mode — a typo'd placeholder that no test would
otherwise notice until the rendered compose file ships a literal `$typo`.

Then, as with every declarative service: add the spec to
`_BUILT_IN_SPECS` in `tests/test_declarative_specs.py`, add the service name
to `tests/test_services_registry.py`, and run the four gates
(`pytest`, `pyright`, `ruff check`, `ruff format --check`).

## What you don't do

- No `DockerComposeService` subclass, no `options.py` companion, no
  framework code. The YAML **is** the plugin.
- No `build:` targets in the template — the framework's versioning story
  (registry tags, GUI picker, recorded selection) only covers pullable
  images. If the upstream stack builds an image, that's a Python plugin.
- No second templating layer. If you find yourself wanting `{{ }}`
  conditionals ("only include this service when auth is enabled"), the answer
  is usually an option with two possible values, not a conditional template.
  When that's not enough, the service graduates to Python.
