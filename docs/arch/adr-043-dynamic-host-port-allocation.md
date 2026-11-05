# ADR-043: Dynamic host-port allocation for declarative services

## Title

Dynamic host-port allocation for declarative services — a `port` option may ask the
framework for a host port with a `$auto_port.<name>` placeholder; the framework
allocates from a dedicated node-level range at service start, persists the
allocation, and materialises the port into the runtime command of whichever
declarative kind owns it (docker, compose, uv).

## Status

Accepted (2026-11-04). The placeholder mechanism in §1-§3 and the decisions Gen
settled on 2026-11-01 are carried through §4-§8 unchanged — the registry file
and its `flock`, resolve-at-start, the `18000-18999` range, the four-way free
check, sticky reassignment — except where §13 supersedes "the worker publishes
`host_port`" (no HTTP API change; discovery is a separate decision) and §9
supersedes "docker-kind first cut" (the marker works for every declarative
kind). §11's open decision was closed on 2026-11-04: the
`write_user_overrides` whole-file wipe is fixed in this task.

History of this ADR:

- Round 1 (2026-10-30) proposed a bare `auto` / `auto:<name>` token outside the `$`
  substitution namespace. Gen rejected it on 2026-11-01: the signifier must be `$`.
- Round 2 (2026-11-01) adopted `$auto_port.<name>` and the existing substitution
  engine, but scoped the mechanism to `kind: docker` and added HTTP API fields.
  Gen rejected it on 2026-11-03: the marker must work for **every** declarative
  kind (including inside compose templates), and the API changes must go — the
  port-assignment change is internal to the worker.
- This revision (round 3) makes the marker kind-agnostic, drops the HTTP API
  change, and adds the compose and uv materialisation paths.
- Gen approved the round-3 plan on 2026-11-04 with every open decision taken
  as recommended: the §11 wipe is fixed here, the fleet-wide spec migration is
  out of scope, only `metube.yaml` and `genesis-productivity.yaml` adopt the
  marker, the uv kind is implemented but not adopted, the use-site
  duplicate-name rejection is in, and the compose runtime-port check is in.
  The ADR was renumbered from 042 to 043 at merge time — 042 was taken by
  ADR-042 (user-tracked llama-cpp forks).

## Context

Every declarative service declares its host port by hand, in `options.listen_port`
of `type: port`, and the YAML comments carry the collision history:

- `jellyfin.yaml`: host 8096 "avoiding llama-swap 8080, bifrost 8090, worker API
  9090, ntfy 8092, and all other in-use ports".
- `metube.yaml`: host 8098 "avoids every other in-use port in this stack".

Sixteen declarative services each carry a hand-picked port. On the development node
today, ten of them sit in the 8000-8101 band (8000, 8081, 8090, 8092, 8096, 8097,
8098, 8099, 8100, 8101); the other six are 2342, 3000, 3001, 4533, 9095 and 11235.
They share the space with the framework's own ports (llama-swap 8080, worker API
20985, worker UI 8501). Adding a service means picking a free number by hand on a
node that may already be running many of them.

The port flows through one path today:

```
port option  ->  resolved option  ->  container.listen_port  ->  docker -p host:host_port:container
```

`genesis_worker/utils/services/loader.py` derives the runtime port from the
resolved option precisely so there is a single source of truth; `_build_port_field`
in `genesis_worker/utils/services/spec.py` types the option as an `int` constrained
to `(0, 65536)`.

### The substitution engine we must plug into

The loader has one substitution mechanism, `resolve_placeholders`, and it runs in
three rounds inside `load_service_spec`:

| Round | Call | What it resolves |
|---|---|---|
| A | `resolve_placeholders(options_dump, ctx, options_dump)` | `$variable` markers **inside option defaults** (`pictures_dir: "$media_vault_path/photos"`) |
| B | `resolve_placeholders(raw, ctx, options_resolved)` | markers in the **raw YAML dict**, with the resolved options as the lookup table (`container.listen_port: "$options.listen_port"` becomes the option's value) |
| C | `resolve_placeholders(spec.model_dump(), ctx, options_resolved)` | a second pass over the **validated** spec dump, catching markers that only appear after validation (a `Path` option default) |

and a fourth, at **start** time, for compose only:
`ComposeService._rendered_template()` calls `resolve_placeholders(..., images=self._image_refs())`
to substitute the `$images.<name>` markers that rounds B and C deliberately left
intact.

The mechanism's own vocabulary is a flat `$` namespace dispatched by
`_resolve_placeholder`: `$state_dir`, `$data_dir`, `$vault_path`,
`$media_vault_path`, `$log_dir`, `$cache_dir`, `$config_dir`, `$options.X`,
`$images.X`. Two behaviours matter for this ADR:

1. **An unknown key survives untouched.** `_resolve_placeholder` ends with
   `return f"${key}"` for a key it does not recognise. This is how `$images.<name>`
   stays intact through the load-time rounds.
2. **A resolution source is supplied per call.** `$images.<name>` resolves only
   when the caller passes `images=`; without it the marker is returned intact.

There is exactly one substitution mechanism and there must stay exactly one. A
prior incident in this codebase added a second templating language (jinja) on top
of the existing substitution; that must not be repeated. The dynamic port has to
be a new **key family in the existing namespace**, resolved by the existing
function, at the existing rounds.

### The three kinds, and where each one materialises its runtime command

The three declarative kinds do not build their runtime command the same way, and
the marker has to land in each of them:

| Kind | Runtime command | Where it is generated | How a port reaches it |
|---|---|---|---|
| `docker` | `docker run -p <host_ip>:<host_port>:<container_port>` | `DockerService.start()` (`docker_service.py:409-410`) | a field of the port map |
| `compose` | the rendered `docker-compose.yml` | `ComposeService.start()` → `_render_compose_file()` → `_rendered_template()` | a substring of the template text |
| `uv` | the tmux command line | `UvService.start()` (`uv_service.py:272-281`) | an argument of the command list |

The compose kind is the awkward one, because the port is not a field of the spec —
it is inside a YAML template string:

```yaml
compose_template: |
  services:
    app:
      ports:
        - "$options.listen_host:$options.listen_port:3000"   # Streamlit UI
        - "$options.listen_host:$options.api_port:8765"      # FastAPI
```

Round B substitutes `$options.listen_port` with the option's *value*. If that
value is the marker `$auto_port.listen`, the template becomes
`"0.0.0.0:$auto_port.listen:3000"` — a marker embedded mid-string. The start-time
render substitutes it again, exactly the way `$images.<name>` is materialised
today. **The chain is: compose template → option value → marker → allocated int.**

Two facts make this work and one fact breaks it today:

- **The marker survives rounds A/B/C untouched** — the unknown-key fallback.
- **A marker embedded in a mixed string is substituted by the mixed-string path**
  of `_resolve_string`, which stringifies the replacement. Verified by tracing the
  real loader with a patched resolver:
  `'"$options.listen_host:$options.listen_port:3000"'` → `'"0.0.0.0:$auto_port.listen:3000"'`
  → `'"0.0.0.0:18000:3000"'`.
- **Today it breaks**, because `_build_compose_config` does
  `listen_port=int(option_port)` (`loader.py:327`) on the resolved option: a
  marker raises `ValueError: invalid literal for int() with base 10: '$auto_port.listen'`
  and the service never loads. The compose path needs the option and the config to
  carry the marker as a marker.

The docker kind has the same shape but stops at a field: `container.listen_port:
"$options.listen_port"` becomes the marker in round B, and `DockerService.start()`
turns it into an int before the port mapper sees it.

### Forces

- **Fixed ports must keep working.** Every existing spec, every user override, and
  every orchestrator route that assumes a known port has to behave exactly as it
  does now.
- **The port must be free in the real world.** The framework's own ports and
  anything else bound on the host count as taken. "Free" cannot mean "unused in the
  framework's bookkeeping".
- **Allocations must be distinct across the whole node**, not just within one
  service, and a service may need several ports (a Streamlit UI and a FastAPI API).
- **The mechanism stays inside the worker.** The port-assignment change is internal
  to this codebase; the HTTP API is not part of it. How the orchestrator learns an
  allocated port is a separate decision (§13).
- **The allocation must survive a worker restart.** The container keeps running
  across a worker restart (docker restart policy); the framework's view of its port
  must match.
- **Two processes, one node.** The worker runs as a Streamlit UI process (8501)
  and a FastAPI API process (20985). Both can start a service, so the allocation
  state must be safe across processes, not just across threads.
- **The option DSL is deliberately closed** (ADR-035). The placeholder has to fit
  the existing `port` type; exotic shapes need a Python `options.py` companion.
- **One substitution mechanism.** No second templating language, no parallel
  resolution path.

## Decision

We will let a declarative service ask the framework for its host port with a
`$auto_port.<name>` placeholder. The marker rides the existing substitution
rounds untouched, and the framework materialises it into an allocated integer at
the moment each kind generates its runtime command.

### 1. The placeholder is a key in the existing `$` namespace

A `port` option's value may be an `int` (a fixed port, exactly as today) or a
placeholder marker:

```
$auto_port.<name>          # <name> matches [a-zA-Z0-9_-]+
```

`$auto_port.web`, `$auto_port.api`, `$auto_port.port-1` are all valid. The marker
is a string that the existing `_PLACEHOLDER_RE` already matches
(`auto_port.web` satisfies `[a-zA-Z_][a-zA-Z0-9_-]*(\.[a-zA-Z_][a-zA-Z0-9_-]*)*`),
so no new syntax is introduced into the template language.

The `<name>` is the identity of the port and the key under which the allocation
is recorded.

A YAML author writes it as an option default:

```yaml
options:
  listen_port:
    type: port
    default: "$auto_port.web"
    ui_label: Web UI port
    ui_group: Network
container:
  name: jellyfin
  listen_port: $options.listen_port
  internal_port: 8096
```

The fixed form and the placeholder form coexist per spec, per option (the default
may be either; a user override may pin an int), and per service.

### 2. The marker rides the existing rounds; one new key family resolves it

**Survival needs no change.** `_resolve_placeholder` already returns `f"${key}"`
for a key it does not recognise, so `$auto_port.web` passes through rounds A, B
and C untouched. Verified against the current loader:

```
round A  ->  {'listen_port': '$auto_port.web', 'pictures_dir': '/tmp/media-vault/photos'}
round B  ->  {'container': {'listen_port': '$auto_port.web', 'internal_port': 8096}}
round C  ->  {'container': {'listen_port': '$auto_port.web'}}
```

**Resolution is one new branch**, mirroring the `$images.<name>` branch exactly:

```python
# in _resolve_placeholder
if key.startswith(AUTO_PORT_KEY_PREFIX):        # "auto_port."
    if ports is None:
        return f"${key}"                        # no resolution source: marker survives
    return ports(key[len(AUTO_PORT_KEY_PREFIX):])
```

`resolve_placeholders`, `_resolve_string` and `_resolve_placeholder` gain one
keyword-only parameter, `ports: Callable[[str], int] | None = None`, alongside the
existing `images` parameter. The load-time rounds do not pass it, so the marker
survives; the start-time call passes it, so the marker becomes a port.

**The allocated port must stay an `int`.** `_resolve_string` preserves the typed
value for a single-placeholder string only when the key starts with `options.`;
everything else is stringified. The port family joins the `options.` case:

```python
if match.group(1).startswith(AUTO_PORT_KEY_PREFIX):
    return resolved          # an allocated port is an int; keep it an int
```

Mixed strings (the compose template's `"$options.listen_host:$options.listen_port:3001"`)
already stringify their substitutions, so the marker embedded in a template
resolves to `"18000"` with no further change.

**No new round, no new mechanism, no new language.** The marker is a key in the
namespace the loader already dispatches on, resolved by the function the loader
already calls, at the rounds the loader already runs.

### 3. The option and spec types learn the marker

`type: port` becomes a union: an `int` constrained to `(0, 65536)`, or a string
matching `^\$auto_port\.[a-zA-Z0-9_-]+$`.

Verified against pydantic 2.13 in this repo's venv:

| Input | Result |
|---|---|
| `8096` (int) | `8096` (int) |
| `"8096"` (a user override from `user-overrides.env`) | `8096` (int) — the string fails the marker pattern, the int branch coerces it in lax mode |
| `"$auto_port.web"` | `"$auto_port.web"` (str, preserved through `model_dump()`) |
| `"garbage"`, `"$auto_port."`, `"$auto_port.web.ui"` | `ValidationError` |
| `0`, `70000` | `ValidationError` (the int branch's `gt` / `lt`) |

One trap: the numeric constraints must sit on the **union's int member**, not on
the field. `Field(default, gt=0, lt=65536)` applied to the union raises
`TypeError: Unable to apply constraint 'gt' to supplied value $auto_port.web`
when a marker is validated — a `TypeError`, not a `ValidationError`, so the
loader's `except ValidationError` would not catch it. The field carries only the
default.

`ContainerSpec.listen_port` takes the same union. `internal_port` stays an `int`:
when `listen_port` is a marker, `internal_port` is **required** (the container
port cannot default to a host port), enforced by a model validator on
`ContainerSpec`.

`ContainerSpec` gains `extra_ports: list[{container: int, host: int | marker}]`,
because the docker kind publishes exactly one host port today and the definition
of done requires a service with two placeholders to come up on two different
ports. The compose kind publishes several ports through its template and needs no
such field.

`ComposeServiceConfig.listen_port` and `UvServiceConfig.listen_port` take the same
union, and `_build_compose_config` stops coercing the resolved option with
`int(...)` — a marker is carried through as a marker.

### 4. The allocator

A new leaf package `genesis_worker/utils/ports/` (framework helpers, importable by
plugins under ADR-009):

- `markers.py` — the grammar, in one place: `AUTO_PORT_KEY_PREFIX = "auto_port."`,
  `AUTO_PORT_MARKER_PATTERN`, `is_marker(value)`, `marker_name(value)`, and
  `assert_no_markers(payload, where)` — the guard that refuses to hand a runtime
  command that still carries a `$auto_port.` marker.
- `allocator.py` — `NodePortAllocator`, owning the node-level registry.
  - `allocate(service, name, own_containers=()) -> int` — the port for one use
    site, allocating one if needed.
  - `allocated(service, name) -> int | None` — the recorded port, or `None`.
  - `release(service, name)` — drop a record (only on explicit request).
  - `snapshot() -> Mapping[str, Mapping[str, int]]` — the whole registry.
- `environment.py` — the host probes the allocator needs: the fixed ports in use
  (framework ports + every registered service's declared port), the declared host
  port bindings of every container, and the live bind test.
- `binder.py` — `PortBinder`, the per-service view of the allocator: it turns a
  port value (`int` or marker) into an int, either by allocating (at start) or by
  looking the record up (anywhere else), and it produces the `ports=` callable
  that the substitution engine consumes.

Persistence: `<state_dir>/port_allocations.yaml`, the worker's global state dir
(next to `enabled_services.yaml` and `catalog.json`):

```yaml
allocations:
  jellyfin:
    web: 18000
  genesis-productivity:
    listen: 18001
    api: 18002
```

Atomic write (sibling `.tmp` + `os.replace`, mode `0600`), the same pattern as
`utils/state/enabled_services.py`.

**Multi-process safety.** The worker is two processes (Streamlit, FastAPI) on one
node, and both may start a service. Every allocate runs inside an exclusive
`fcntl.flock` and re-reads the file inside the lock, so the two processes
cannot hand out the same port.

The lock lives on a sibling file (`port_allocations.lock`), not on the
registry file itself: the registry is written atomically (sibling `.tmp` +
`os.replace`), which swaps the inode, and a lock held on the old inode would
not exclude the next writer.

The three probes the allocator needs — the fixed ports, the container
bindings, the live bind test — are injected, so the allocator's own tests
never touch the docker daemon and never bind a real port.

### 5. What "free" means

A candidate port is free for the service asking for it only if all of the
following hold:

1. **Not allocated by another service** on the node (the registry — every other
   service's records, including this service's other use sites).
2. **Not declared as a fixed port by any registered service**, and not one of the
   framework's own ports (worker API 20985, worker UI 8501, plus any
   `GENESIS_API_PORT` / `GENESIS_UI_PORT` override). These services are not
   necessarily running, so the bind check below would not catch them.
3. **Not declared by a container that is not ours.** Docker's
   `HostConfig.PortBindings` is consulted for every container, running or stopped.
   On docker 29.9.2 (verified on the development node) a stopped container does
   *not* actually hold its port — the network sandbox is torn down and the host
   port is released — but the declared binding survives, and a later start of that
   container would collide with whoever took the port. Reserving declared bindings
   is the conservative choice.
4. **Bindable right now** — unless one of *our own* containers already publishes
   it. The allocator binds a throwaway socket on the address the service will
   publish on (`0.0.0.0` by default), with `SO_REUSEADDR` set to mirror
   `docker-proxy`, and releases it immediately. `EADDRINUSE` means the port is not
   free.

Rule 4's exception is what makes the sticky behaviour work: a service that is
already running holds its own port, so a plain bind test would fail and the
service would be moved to a different port on every restart. The caller tells the
allocator which containers are its own — the docker kind knows its container name,
the compose kind knows its project name (`<service>-stack`, which compose labels
every container with).

Selection walks the range in order and takes the first port that passes all four
checks.

### 6. The range

A dedicated range, `18000-18999`, outside the Linux ephemeral range
(`32768-60999` on the development node, `net.ipv4.ip_local_port_range`). The kernel
hands ephemeral ports to outbound connections; a service bound to one of those
would intermittently fail to accept a connection.

The ephemeral range is not used. The IANA dynamic range (49152-65535) is not used
either — it overlaps the ephemeral range on Linux.

The range is a constructor argument with a module-level default, so the framework
can widen or move it without touching the allocator.

### 7. When the port is resolved

At service start, at the point each kind generates its runtime command. Before
that, the marker is carried as a marker.

```python
# DockerService.start()
resolved = resolve_placeholders(
    {"primary": self.config.listen_port,
     "extra": [{"container": b.container_port, "host": b.host} for b in self.config.extra_ports]},
    self._ctx, {}, ports=self._port_binder().resolver(),
)
```

```python
# ComposeService.start() — the same engine, the same call shape as the
# image materialisation that already happens here.
body = resolve_placeholders(
    self.config.compose_template, self._ctx, {}, None,
    images=self._image_refs(), ports=self._port_binder().resolver(),
)
```

```python
# UvService.start()
command = resolve_placeholders(
    self.config.command, self._ctx, {}, ports=self._port_binder().resolver(),
)
```

The resolved values are `int`s; the container's port mapper, the compose file and
the command line never see a marker. `assert_no_markers` re-checks the materialised
payload and raises if a `$auto_port.` marker survived — a marker in a place the
framework does not materialise (an `env` value, an `extra_arg`) is a spec error,
not a silent broken command.

Resolving at load time instead was considered and rejected: it burns a port for
services that are never started, and it allocates before the user has done
anything.

### 8. How the service reaches the allocator, and the single source of truth

Through the context, which is how the framework hands plugins everything they need
(ADR-009):

- `genesis_worker/contracts/ports.py` declares a `PortAllocator` `Protocol`.
- `ServiceContext` gains `port_allocator: PortAllocator | None = None`.
- `ServiceRegistry` constructs one allocator per worker (state dir + a
  reserved-port provider + a container-binding probe) and passes it into every
  service context.

The context carries the *handle*; the substitution call decides whether to
*resolve*. The load-time rounds pass no resolution source, so the marker survives;
the start-time call passes one, so it resolves. That separation is what keeps the
allocation at start time without a second mechanism.

`DeclarativeServiceBase` gains two accessors:

```
declared_host_port() -> int | None   # the fixed port this service declares; None when it asked for one
effective_host_port() -> int | None  # the port it actually uses: fixed, allocated, or None
```

`effective_host_port()` returns the allocated port even before this process
started the service, because the allocator reads the persisted registry. Every
runtime site that today reads `config.listen_port` — the docker port mapper, the
compose render, the health probe, `status()`, `web_ui_endpoint()`,
`listen_address` — switches to the effective port. When the service asked for a
port that has not been allocated yet, the effective port is `None` and the
endpoint fields are `None` rather than a wrong number.

`InferenceService` gains one non-abstract method, `declared_host_port() -> int |
None`, defaulting to `None`. It is how the allocator learns which fixed ports to
stay off: the framework asks every registered service, and the declarative base
answers with its resolved `listen_port` when that is an int.

The registry file is the record; the running container is the truth. The
allocation is *not* written back into the options model: the configure panel
renders the options model, so a written-back port would read as a declared value
and the next Apply would pin it.

### 9. Materialisation, per kind

| Kind | Marker lives in | Materialised at | Effective port used by |
|---|---|---|---|
| `docker` | `container.listen_port`, `container.extra_ports[].host`, or the `port` option they reference | `DockerService.start()` — the `-p` map | the port map, the health probe, `status()`, `web_ui_endpoint()` |
| `compose` | the `port` option, referenced from `compose_template` as `$options.X`, or written literally in the template | `ComposeService.start()` — the rendered `docker-compose.yml` | the health probe, `status()`, `web_ui_endpoint()` |
| `uv` | the `port` option, referenced from `command`, or written literally in the command | `UvService.start()` — the tmux command line | the health probe, `status()`, `web_ui_endpoint()` |

The compose kind's runtime port follows the `listen_port` option, exactly as it
does today (`_build_compose_config` derives it from the resolved option so there
is a single source of truth). When that option carries a marker, the effective
port is the allocation for that marker name — the same allocation the template
publishes, because the template resolves `$options.listen_port` to the same
marker. The loader checks this: the marker of the runtime port must appear in the
resolved template, or the spec is rejected with a message naming the option and
the marker.

### 10. One marker name, one port

Within a service, a marker name identifies one port. The loader collects the
*use sites* of a spec:

- each `port`-typed option whose value is a marker;
- each port-bearing spec field (`container.listen_port`,
  `container.extra_ports[].host`) whose **literal** value is a marker — a field
  written as `$options.X` is not a use site, it is a reference to one;
- for the compose kind, each literal marker in `compose_template` whose name no
  option already declares.

Two use sites may not share a name: the loader rejects the spec and names both
sites. Two *references* to the same use site are fine — that is the whole point
of the name-as-identity rule, and it is what makes
`options.listen_port: "$auto_port.listen"` plus a template that publishes
`$options.listen_port` work.

The compose template is a rendering surface, not a declaration: a literal marker
in it that no option declares becomes a use site of its own (and gets its own
allocation).

### 11. The `write_user_overrides` whole-file wipe is fixed here

`write_user_overrides` replaces the file, and the configure panel writes only the
current service's scalars, so an Apply on one service wipes every other
service's overrides. The new "assign automatically" control makes this worse: the
panel must be able to write *nothing* for an option, which with a whole-file
replace means "leave the existing key alone" — a scoped read-modify-write.

Gen's decision (2026-11-04): **fix here.** The panel's `_persist` does a
read-modify-write scoped to the service's own scalar-option keys: other
services' keys and non-service keys are preserved, and an option the panel
deliberately leaves out (an auto-assigned port) loses its pinned override so
the YAML default — the marker — survives. The alternative (own task) was
rejected: shipping the "assign automatically" control on top of a known wipe
ships the bug into a new feature.

### 12. The configure panel

A `port` option whose current value is a marker must not render as a
`number_input` seeded with `0` (what the current widget would do with a
non-int value). The panel renders the effective port as a read-only line plus an
"assign automatically" checkbox; while the checkbox is ticked, Apply writes
nothing for that option, so the YAML default (the marker) survives. Unticking it
and applying pins an ordinary fixed port.

This phase depends on the §11 decision — resolved: the wipe is fixed here.

### 13. Discovery is out of scope

The orchestrator's reverse proxy routes by hostname to a `node:port` target, and
an allocated port is not known ahead of time. Gen's decision on 2026-11-03: the
port-assignment change is internal to the worker, and the orchestrator's
discovery needs are a separate decision.

So this ADR adds **no** HTTP API field. The mechanism leaves the port observable
in three places, none of which requires an API change:

- the service's own `status()` / `web_ui_endpoint()` — already part of the API's
  payload and already carrying the effective port;
- the node-local registry file `<state_dir>/port_allocations.yaml`;
- the rendered `docker-compose.yml` and the container's own port bindings.

Whichever surface the orchestrator ends up using, the mechanism does not change.

### 14. Documentation

`docs/tutorials/declarative-services.md` gains a section on the placeholder form,
the allocation range, the persistence file, and the per-kind materialisation
points. The "Limitations" section loses its "Dynamic port discovery — `listen_port`
is a fixed integer" bullet.

## Consequences

**Positive**

- Adding a service no longer means picking a free port by hand, and the YAML
  comments that record the collision history can go away.
- The placeholder is one key family in the substitution namespace the templates
  already use. No second template language, no new round, no parallel resolution
  path.
- The marker works wherever a port is meaningful: a docker port map, a compose
  template, a uv command line. One mechanism, three materialisation points.
- The allocation is real: it accounts for the framework's own ports, other
  services' fixed ports, container bindings, and anything bound on the host.
- The allocation survives a worker restart, so a service keeps its port across a
  worker restart and a compose stack keeps its published ports.
- Fixed ports are untouched. Every existing spec keeps working exactly as it does.
- Nothing changes on the HTTP API.

**Negative**

- The `port` option type stops being a plain `int`. The union's numeric
  constraints must sit on the int member; putting them on the field raises a
  `TypeError` on a marker rather than a `ValidationError`.
- The effective port is a moving target for a service that asked for one: it can
  change between runs if its previous port was taken. Anything that hard-codes the
  port (bookmarks, the reverse proxy's generated config) has to re-read it.
- A service that asks for a port has no endpoint until it has started once. The
  status payload reports `None` rather than a wrong number.
- A second persistence file at the worker's state level, next to
  `enabled_services.yaml`.
- The allocator needs an `fcntl` lock and a file re-read, because the worker is
  two processes on one node. Locking the registry file alone is not enough — the
  in-memory view has to be refreshed from disk inside the lock.
- The "declared but stopped" container rule is conservative: it keeps ports
  unavailable that docker would in fact release. It trades a rare port for a rare
  "somebody stole my port" failure.
- `resolve_placeholders` grows a parameter. It stays one function with one
  namespace, but the signature now carries two optional resolution sources.
- A plugin that does not implement `declared_host_port()` does not have its port
  reserved. The bind test still protects it while it is running.
- The compose kind's runtime port follows the `listen_port` option, so a compose
  spec that publishes its port only in the template has to declare the option too
  — the loader now rejects the mismatch instead of failing later at the probe.

**Neutral**

- The port range is a policy value, not a contract. It moves if the fleet's needs
  change.
- The kind-agnostic mechanism is one substitution branch and one extra parameter;
  the per-kind work is a materialisation call at each runtime-command site.
- The orchestrator's discovery surface is not decided here (§13).

## Plan

`docs/arch/plans/plan-043-dynamic-host-port-allocation.md` — the approved
round-3 plan (Gen, 2026-11-04), implemented in task `t-sgqw36`.
