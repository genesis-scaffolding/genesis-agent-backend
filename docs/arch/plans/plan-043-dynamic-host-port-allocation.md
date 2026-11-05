# Plan — dynamic host-port allocation for declarative services (round 3)

> **Approved and implemented.** Gen approved this plan as-is on 2026-11-04, with
> every open decision taken as recommended (§10). Task `t-sgqw36` implemented
> phases 1-8 against this plan; the ADR was renumbered to
> `adr-043-dynamic-host-port-allocation.md` at merge time (042 was taken by
> ADR-042, user-tracked llama-cpp forks). The text below is the plan as
> approved — the "design-only" framing describes the state at the time of
> writing, not the shipped state.

Task: `t-sgqw36` — Design a mechanism to avoid port collision on a single node.
Worktree: `/home/gentran/Documents/worktrees/genesis-agent-backend/t-sgqw36-port-collision`
Branch: `se/t-sgqw36-port-collision` (nothing committed).
ADR: `docs/arch/adr-043-dynamic-host-port-allocation.md` (Proposed, uncommitted, **updated
in place by this round**).

This is a **design-only** task. Nothing below is implemented; the phases below are
the implementation plan for the follow-up task.

---

## 0. The fork line (offered once)

Per the shared protocol, this reworked plan re-enters the gate with the fork
offered to the human. The rework proceeds either way; the fork is a choice, not a
blocker.

- **Split** — land the docker path first, the compose path as a second plan.
- **Scope down** — drop the uv kind, drop `extra_ports`, drop the configure-panel
  phase.
- **Change approach** — materialise at load time instead of start time; or give the
  marker its own option type instead of a union on `type: port`.
- **Drop** — abandon the task.

**My recommendation: none of them — take the plan as it stands.** The compose path
is not a separate mechanism; it is the same substitution branch with a different
materialisation point, and the two chains Gen named are the same chain with a
different carrier (§2). Splitting it would produce two plans to review for one
mechanism. I *am* scoping down one thing on my own initiative: the uv kind has no
spec in the repo today, so it is implemented and tested but not adopted (§8).

---

## 1. What the second rejection changed, and what it did not

**Changed #1 — the mechanism is now kind-agnostic.** Round 2 scoped the marker to
`kind: docker` and made the compose kind raise "not supported yet". That is
gone. `$auto_port.<name>` is materialised wherever a host port is meaningful:
the docker port map, the compose template, the uv command line. One substitution
branch, one extra parameter on the existing `resolve_placeholders`, three
materialisation points.

**Changed #2 — the HTTP API change is dropped.** Round 2's Phase 6 added
`host_port` to `ServiceSummarySchema` / `ServiceDetailSchema` and filled it from
`svc.host_port()`. That phase is gone. Gen's question was *"why is any API change
needed?"* — the honest answer is **none is needed for this mechanism**, and §7
answers it directly: the port-assignment change is internal to the worker, and the
mechanism leaves the allocated port observable in three places that already exist
(the service's own `status()` / `web_ui_endpoint()`, the node-local registry file,
and the container's own port bindings). Which surface the orchestrator reads is
the separate decision Gen named; the mechanism does not change whichever way that
goes.

**Unchanged (settled by Gen on 2026-11-01, carried in verbatim):** the registry
file + `flock`; resolve-at-start; the `18000-18999` range; the four-way free
check; sticky reassignment; design-only scope.

**Superseded by the rejection:** "worker publishes `host_port`" (→ §7, no API
change) and "docker-kind first cut" (→ §2, all kinds).

**One factual correction to the brief, carried over from round 2.** The brief
describes the loader as "resolving placeholders in multiple rounds at start time:
options first, then into the spec/template later at service start". In the code,
rounds A/B/C run at **load** time inside `load_service_spec`; the only start-time
round is the compose template render (`ComposeService._rendered_template`). The
design follows the brief's actual instruction — plug into the existing rounds —
and it works because the mechanism's own "unknown key" fallback already preserves
a marker it does not recognise, exactly as it preserves `$images.<name>` today.
The round table below is what the code does, verified against the real loader.

| Round | Where | Call | Resolves |
|---|---|---|---|
| A | load | `resolve_placeholders(options_dump, ctx, options_dump)` | `$variable` markers inside option defaults |
| B | load | `resolve_placeholders(raw, ctx, options_resolved)` | markers in the raw YAML dict, options as the lookup table |
| C | load | `resolve_placeholders(spec.model_dump(), ctx, options_resolved)` | a second pass over the validated spec dump |
| D | start (compose only) | `resolve_placeholders(template, ctx, {}, None, images=refs)` | the `$images.<name>` markers B and C left intact |

Round D is the round the port marker joins: the same call, one more resolution
source.

---

## 2. The mechanism, end to end

### 2.1 The marker

```
$auto_port.<name>          # <name> matches [a-zA-Z0-9_-]+
```

A key in the existing `$` namespace, matched by the existing `_PLACEHOLDER_RE`,
resolved by the existing `_resolve_placeholder`, at the existing rounds. **No
second substitution mechanism is introduced** — this is the standing hard
constraint, and the shape of the change is exactly the shape of the existing
`$images.<name>` family: one branch in `_resolve_placeholder`, one optional
resolution source on `resolve_placeholders`.

### 2.2 Gen's two chains, traced against the real loader

I traced both chains by monkey-patching the real loader in memory (no repo
change) with the new branch in place, and loading the two real spec shapes. The
output is in §2.5.

**Chain A — docker (`metube.yaml`).** The marker lives in the option; the option
value flows into the spec field.

```
YAML    options.listen_port.default = "$auto_port.listen"
        container.listen_port        = "$options.listen_port"
                                     internal_port: 8081

A       options_dump        ->  {'listen_port': '$auto_port.listen', ...}
B       raw                 ->  container.listen_port = '$auto_port.listen'
        pydantic            ->  listen_port accepts int | marker
C       spec dump           ->  marker survives
        config.listen_port  =  '$auto_port.listen'          (int when fixed)

start   resolve_placeholders({'primary': '$auto_port.listen'}, ctx, {}, ports=resolver)
     ->  {'primary': 18000}                               (an int, not a string)
     ->  docker -p 0.0.0.0:18000:8081
```

**Chain B — compose (`genesis-productivity.yaml`).** The marker lives in the
option; the option value flows into the *template text*.

```
YAML    options.listen_port.default = "$auto_port.listen"
        options.api_port.default    = "$auto_port.api"
        compose_template:
            - "$options.listen_host:$options.listen_port:3000"   # Streamlit UI
            - "$options.listen_host:$options.api_port:8765"      # FastAPI

A       options_resolved     ->  {'listen_port': '$auto_port.listen',
                                   'api_port': '$auto_port.api'}
B       compose_template     ->  '... "0.0.0.0:$auto_port.listen:3000" ...'
                                '... "0.0.0.0:$auto_port.api:8765" ...'
C       marker survives in the template and in config.listen_port
        config.compose_template = '... $auto_port.listen ... $auto_port.api ...'
        config.listen_port      = '$auto_port.listen'

start   resolve_placeholders(template, ctx, {}, None, images=refs, ports=resolver)
     ->  '... "0.0.0.0:18000:3000" ...'
        '... "0.0.0.0:18001:8765" ...'
     ->  written to <state_dir>/compose/genesis-productivity-stack/docker-compose.yml
```

This is Gen's chain verbatim: **compose template → option value → marker →
allocated int**. The option value carrying a marker *is itself resolved* — at
render time, by the same engine, in the same call that materialises the images.

### 2.3 The three properties that make it work without touching the mechanism's shape

1. **Survival is free.** `_resolve_placeholder` ends with `return f"${key}"` for a
   key it does not recognise. `$auto_port.listen` passes through rounds A/B/C
   untouched — verified against the real loader, output in §2.5.

2. **Resolution is one branch, mirroring `$images.<name>`.** A resolution source
   supplied per call: absent at load time (the marker survives), present at start
   time (the marker becomes a port).

   ```python
   if key.startswith(AUTO_PORT_KEY_PREFIX):        # "auto_port."
       if ports is None:
           return f"${key}"                        # no resolution source: marker survives
       return ports(key[len(AUTO_PORT_KEY_PREFIX):])
   ```

3. **The allocated port stays an `int`.** `_resolve_string` preserves the typed
   value for a single-placeholder string only when the key starts with `options.`;
   everything else is stringified. The port family joins the `options.` case:

   ```python
   if match.group(1).startswith("options.") or match.group(1).startswith(AUTO_PORT_KEY_PREFIX):
       return resolved          # an allocated port is an int; keep it an int
   ```

   Mixed strings (the compose template) already stringify their substitutions, so
   a marker embedded in a template resolves to `"18000"` with no further change.

### 2.4 One more property that only the compose path forces

`_build_compose_config` currently derives the runtime port with
`listen_port=int(option_port)` (`loader.py:327`). With a marker that raises
`ValueError: invalid literal for int() with base 10: '$auto_port.listen'` and the
service never loads. So the compose path needs three type changes, not one:

- `ComposeServiceConfig.listen_port: int | str`;
- `_build_compose_config` carries the marker through instead of coercing it;
- the compose service's runtime port (health probe, `status()`,
  `web_ui_endpoint()`) reads the *effective* port, which for a marker is the
  allocation for that marker name.

The runtime port of a compose service follows the `listen_port` option, exactly as
today — that is the existing single-source-of-truth rule
(`_build_compose_config` derives it from the resolved option so the probe and the
container can never desync). With a marker, "the resolved option" *is* the
allocation, so the rule still holds. The loader enforces it: **the marker of the
compose runtime port must appear in the resolved template**, or the spec is
rejected naming the option and the marker. Without that check, a compose spec
that publishes `$auto_port.api` but probes `listen_port` would probe a port
nobody published — a silent failure that shows up as "the service never becomes
ready".

### 2.5 The trace, verbatim

Traced by monkey-patching the real loader in memory with the new branch (the
patch is in `/tmp/scratch_trace.py`, nothing in the repo changed):

```
===== DOCKER =====
  config.listen_port = '$auto_port.listen'
  resolved at start  = {'primary': 18000}
===== COMPOSE =====
  config.listen_port = '$auto_port.listen'
  template (loaded) =
    'version: "3.8"\nservices:\n  app:\n    image: $images.app\n    ports:\n      - "0.0.0.0:$auto_port.listen:3000"\n      - "0.0.0.0:$auto_port.api:8765"\n'
  template (rendered) =
    'version: "3.8"\nservices:\n  app:\n    image: repo:tag\n    ports:\n      - "0.0.0.0:18000:3000"\n      - "0.0.0.0:18001:8765"\n'
```

The compose run needed one extra patch to get that far: the `int(option_port)`
crash described in §2.4. That patch is the design change, not a workaround.

---

## 3. Where the port is materialised, per kind

"Materialise at the point the runtime command is generated" — Gen's phrasing, and
the same place images are materialised in the compose path.

| Kind | Marker lives in | Materialised at | The effective port is used by |
|---|---|---|---|
| `docker` | `container.listen_port`, `container.extra_ports[].host`, or the `port` option they reference | `DockerService.start()` — the `-p` map (`docker_service.py:409-410`) | the port map, the health probe, `status()`, `web_ui_endpoint()`, `listen_address` |
| `compose` | the `port` option, referenced from `compose_template` as `$options.X`, or written literally in the template | `ComposeService.start()` → `_render_compose_file()` → `_rendered_template()` | the health probe, `status()`, `web_ui_endpoint()` |
| `uv` | the `port` option, referenced from `command`, or written literally in the command | `UvService.start()` — the tmux command line (`uv_service.py:272-281`) | the health probe, `status()`, `web_ui_endpoint()` |

Three call sites, one shared helper, one substitution branch.

**The uv kind.** There is no `kind: uv` spec in the repo today (13 docker, 3
compose), but the kind exists and the requirement is "all declarative kinds". A
uv service's port comes from its own command line, and the command line is
declarative — `command: ["serve", "--port", "$options.listen_port"]` — so the
framework *can* inject an allocated port: materialise the command list at start,
the same way the compose template is materialised. The uv service's
`listen_port` is the framework's probe port and follows the same option, so the
probe and the process agree. Implemented and tested; not adopted (no spec
changes).

**The docker kind's second port.** The docker model publishes exactly one host
port. The definition of done requires "a service with two placeholders comes up
on two different ports", so `ContainerSpec` gains `extra_ports:
list[{container: int, host: int | marker}]` and the port map is built from the
primary plus the extras. The compose kind publishes several ports through its
template and needs no such field.

**The guard.** After materialisation, `assert_no_markers(payload, where)`
re-scans the runtime command for a surviving `$auto_port.` marker and raises. A
marker in a place the framework does not materialise (an `env` value, an
`extra_arg`, a `command` element of a service that was not materialised) is a
spec error, not a silent broken command. The scan is on the `$auto_port.` prefix
specifically, so an unrelated `$SOMETHING` in an env value is not a false
positive.

---

## 4. The registry, the algorithm, the failure modes

### 4.1 The registry file

`<state_dir>/port_allocations.yaml` — the worker's global state dir, next to
`enabled_services.yaml` and `catalog.json`:

```yaml
allocations:
  metube:
    listen: 18000
  genesis-productivity:
    listen: 18001
    api: 18002
```

Atomic write (sibling `.tmp` + `os.replace`, mode `0600`), the pattern from
`utils/state/enabled_services.py`.

**Multi-process safety.** The worker is two processes (Streamlit 8501, FastAPI
20985) on one node, and both can start a service. Every mutation: open the file,
`fcntl.flock(LOCK_EX)`, **re-read from disk inside the lock**, mutate, write,
unlock. The in-memory view is refreshed from disk inside the lock — locking the
file alone is not enough.

### 4.2 The free check (four ways, as settled)

A candidate port is free *for the service asking for it* only if:

1. **Not allocated by another service** — the registry, excluding this service's
   own record for this use site (that is the sticky candidate) but including this
   service's *other* use sites.
2. **Not a fixed port of any registered service, and not a framework port** —
   worker API 20985, worker UI 8501, plus any `GENESIS_API_PORT` /
   `GENESIS_UI_PORT` override. These services need not be running, so the bind
   test would not catch them. The framework asks every registered service for
   its `declared_host_port()`.
3. **Not declared by a container that is not ours** — `HostConfig.PortBindings`
   of every container, running or stopped. On docker 29.9.2 a stopped container
   does *not* actually hold its port (the sandbox is torn down and the host port
   is released), but the declared binding survives, and a later start of that
   container would collide with whoever took the port. Declared bindings are
   reserved.
4. **Bindable right now** — a throwaway socket on the publish address
   (`0.0.0.0`) with `SO_REUSEADDR` (mirroring `docker-proxy`), released
   immediately; `EADDRINUSE` means not free. **Exception: if one of *our own*
   containers already publishes the port, it counts as free for us.**

Rule 4's exception is new in round 3 and it is what makes stickiness work for the
compose path: a running stack holds its own published ports, so a plain bind test
would fail and every restart would move the service to a different port. The
caller tells the allocator which containers are its own:

- docker: the container name (`config.container_name`);
- compose: the containers of the compose project (`<service>-stack`), which
  compose labels on every container — so the probe reads the label, not the
  template.

Selection walks the range in order and takes the first port that passes all four
checks.

### 4.3 The allocation algorithm

```
allocate(service, name, own_containers):
    flock(LOCK_EX)
    re-read the registry from disk
    record = registry[service][name]
    if record is not None and free_for_us(record, service, name, own_containers):
        unlock; return record                      # sticky
    for port in 18000..18999:
        if free_for_us(port, service, name, own_containers):
            registry[service][name] = port
            write atomically
            unlock; return port
    unlock; raise RuntimeError("the dynamic port range is exhausted")
```

`free_for_us` is §4.2. The sticky rule is the settled one: the persisted port is
reused on the next start if it is still ours (published by our own container) or
still free; otherwise a new port is allocated and the record updated.

**Records are not released on stop.** The record is what makes the port survive a
worker restart and a stop/start cycle; releasing it would let the port drift.
`release(service, name)` exists for the explicit case: the configure panel pins
a fixed port, the service no longer asks for one, and the record is stale. The
`ServiceRegistry.rebuild` path reconciles: after a rebuild, the records whose
marker names the service no longer declares are released. That is hygiene, not
correctness — without it the range leaks slowly.

### 4.4 The range

`DYNAMIC_PORT_RANGE = (18000, 18999)`, outside the Linux ephemeral range
(`32768-60999` on this node). Constructor-overridable.

### 4.5 Failure modes

| Failure | Behaviour |
|---|---|
| The service asks for a port and no allocator is wired | `RuntimeError` at start, naming the service and the marker. Not a silent fallback to a fixed port. |
| The range is exhausted | `RuntimeError` at start, naming the range. |
| The recorded port was taken by someone else | Reallocate; the record is rewritten. The service's port changes — anything holding it must re-read. |
| The service has never started | The effective port is `None`. `status()` and `web_ui_endpoint()` report `None` rather than a wrong number. |
| The compose template publishes a different port than the runtime port probes | Rejected at load, naming the option and the marker. |
| Two use sites share a marker name | Rejected at load, naming both sites. |
| A marker survives into a runtime command | `assert_no_markers` raises at start, naming the payload. |
| Two worker processes allocate at the same moment | `flock` + re-read inside the lock. |
| The registry file is corrupt | Treated as empty, like `load_enabled_set` does — the next mutation rewrites it cleanly. |

---

## 5. One marker name, one port

New in round 3, because the compose path makes "where does a marker appear?" a
real question. Within a service, a marker name identifies one port.

**Use sites** (collected by the loader from the *raw* YAML, before substitution):

- each `port`-typed option whose value is a marker;
- each port-bearing spec field (`container.listen_port`,
  `container.extra_ports[].host`) whose **literal** value is a marker — a field
  written as `$options.X` is not a use site, it is a *reference* to one;
- for the compose kind, each literal marker in `compose_template` whose name no
  option already declares (the template is a rendering surface, not a
  declaration).

**Two use sites may not share a name.** The loader rejects the spec and names
both. Two *references* to the same use site are fine — that is the point of the
name-as-identity rule, and it is exactly what makes
`options.listen_port: "$auto_port.listen"` plus a template publishing
`$options.listen_port` work.

Worked examples:

| Spec | Use sites | Verdict |
|---|---|---|
| metube: option `listen_port` = `$auto_port.listen`; `container.listen_port` = `$options.listen_port` | the option only (the field is a reference) | one port ✓ |
| genesis-productivity: options `listen_port` = `$auto_port.listen`, `api_port` = `$auto_port.api` | two options, two names | two ports ✓ |
| a docker spec with `container.listen_port: "$auto_port.web"` and `container.extra_ports[0].host: "$auto_port.web"` | two fields, one name | **rejected** ✓ |
| a compose template with a literal `$auto_port.db` and no option declaring it | the template | its own port ✓ |

The round-1 rule ("two placeholders in one service must have different names") is
unchanged; this is that rule made precise against a chain where the same marker
legitimately appears in several places.

---

## 6. Files and areas touched

### New

| File | Contents |
|---|---|
| `genesis_worker/utils/ports/__init__.py` | exports |
| `genesis_worker/utils/ports/markers.py` | the grammar in one place: `AUTO_PORT_KEY_PREFIX`, `AUTO_PORT_MARKER_PATTERN`, `is_marker`, `marker_name`, `assert_no_markers` |
| `genesis_worker/utils/ports/allocator.py` | `NodePortAllocator` — the registry file, `flock`, the free check, sticky reuse |
| `genesis_worker/utils/ports/environment.py` | the host probes: fixed ports, container port bindings, the live bind test |
| `genesis_worker/utils/ports/binder.py` | `PortBinder` — the per-service view: materialise / look up / produce the `ports=` callable |
| `genesis_worker/contracts/ports.py` | the `PortAllocator` `Protocol` (contracts stay import-free of utils) |
| `genesis_worker/tests/test_port_allocator.py` | allocator tests |
| `genesis_worker/tests/test_port_markers.py` | grammar + use-site rule tests |
| `genesis_worker/tests/test_compose_service_ports.py` | the compose chain, end to end |

### Modified

| File | Change |
|---|---|
| `genesis_worker/utils/services/loader.py` | the `auto_port.` branch in `_resolve_placeholder`; the `ports=` parameter threaded through `resolve_placeholders` / `_resolve_string` / `_resolve_placeholder`; typed-value preservation for the port family; the use-site uniqueness check; the compose runtime-port check; the config builders carry `int \| str` |
| `genesis_worker/utils/services/spec.py` | the `port` option type becomes `int \| marker`; `ContainerSpec.listen_port` accepts the marker; `ContainerSpec.extra_ports`; `internal_port` required when `listen_port` is a marker |
| `genesis_worker/utils/services/base.py` | `declared_host_port()` / `effective_host_port()`; the `PortBinder` handle; `own_container_names()` |
| `genesis_worker/utils/services/docker_service.py` | the port map is materialised at start; `listen_address` / `web_ui_endpoint` / `status()` / `wait_ready()` read the effective port; `own_container_names()` |
| `genesis_worker/utils/services/compose_service.py` | the template is materialised at start with the port resolver alongside the image refs; the runtime port reads the effective port; `own_container_names()` (the compose project) |
| `genesis_worker/utils/services/uv_service.py` | the command list is materialised at start; the runtime port reads the effective port |
| `genesis_worker/utils/services/configure_panel.py` | the marker-aware `port` widget; the scoped read-modify-write (pending the §9 decision) |
| `genesis_worker/utils/process/docker.py` | `container_port_bindings()` — every container's declared host-port bindings and its compose project label |
| `genesis_worker/contracts/context.py` | `ServiceContext.port_allocator: PortAllocator \| None = None` |
| `genesis_worker/contracts/service.py` | `InferenceService.declared_host_port() -> int \| None` (non-abstract, default `None`) — how the allocator learns which fixed ports to stay off |
| `genesis_worker/registries.py` | the registry builds one allocator and passes it into every `ServiceContext`; `rebuild` reconciles stale records |
| `genesis_worker/services/llama_swap/service.py`, `services/comfyui/service.py`, `services/cptr/service.py` | implement `declared_host_port()` so their fixed ports are reserved |
| `genesis_worker/tests/_factories.py` | `service_ctx(..., port_allocator=None)` |
| `docs/tutorials/declarative-services.md` | the placeholder form, the range, the persistence file, the per-kind materialisation points; the "Dynamic port discovery" limitation bullet goes |
| `genesis_worker/services/_declarative/metube.yaml`, `genesis-productivity.yaml` | the two reference adoptions (one per kind) |

**No change** to `genesis_worker/api/schemas.py`, `genesis_worker/api/routes/services.py`,
or any other API surface. That is the point of §7.

---

## 7. The API question, answered

> *Gen: why is any API surface change needed? This port-assignment change should
> be internal to the worker codebase.*

**It is not needed, and there is none.** Round 2's Phase 6 is dropped: no
`host_port` on `ServiceSummarySchema`, no `host_port` on `ServiceDetailSchema`,
no route change, no schema change.

The reasoning:

- The *mechanism* needs to know the allocated port inside the worker — to build
  the runtime command, to probe the service, to report the endpoint. That is the
  internal `effective_host_port()` accessor on the service, plus the allocator's
  persisted record. No HTTP surface is involved.
- The *orchestrator* needs to know the allocated port — but Gen has already
  decided that is a separate decision, in the toolkit repo, about which surface
  the orchestrator reads. Whatever it picks, the worker already produces the
  number.

What the mechanism leaves observable, without any API change:

| Surface | What it carries |
|---|---|
| `GET /v1/services` / `/v1/services/{name}` — `web_ui_endpoint` and `status().endpoint` (existing fields) | the effective port, embedded in the URL, for a running service |
| `<state_dir>/port_allocations.yaml` (new, node-local) | the service → marker → port map, for every service that ever asked |
| the container's own port bindings (`docker inspect`, the rendered `docker-compose.yml`) | what is actually published |

The one honest gap: a service that has never started has no port, so the
existing endpoint fields are `None`. If the orchestrator needs a port for a
*stopped* service, that is the discovery decision — not this mechanism.

---

## 8. Phase by phase

### Phase 1 — the marker grammar

`genesis_worker/utils/ports/markers.py`: `AUTO_PORT_KEY_PREFIX = "auto_port."`,
`AUTO_PORT_MARKER_PATTERN = r"^\$auto_port\.[a-zA-Z0-9_-]+$"`,
`is_marker(value) -> bool`, `marker_name(value) -> str | None`, and
`assert_no_markers(payload, where)` — the guard that refuses to hand a runtime
command that still carries a `$auto_port.` marker. Small, first, because
everything else uses it.

### Phase 2 — the allocator and the environment

`genesis_worker/utils/ports/allocator.py` and `environment.py`, per §4. The
environment's three probes:

- `fixed_ports()` — the framework's own ports (20985 / 8501 plus the env
  overrides) and every registered service's `declared_host_port()`;
- `container_port_bindings()` — `docker ps -aq` then one `docker inspect` per
  container, returning each container's name, its declared host-port bindings
  (`HostConfig.PortBindings`), and its compose project label
  (`Config.Labels["com.docker.compose.project"]`);
- `bindable(port)` — the `SO_REUSEADDR` socket bind test.

### Phase 3 — the substitution engine

`genesis_worker/utils/services/loader.py`:

1. `resolve_placeholders(value, ctx, options, auth_token=None, images=None, ports=None)`
   — one new keyword-only parameter, mirroring `images`.
2. `_resolve_placeholder` — one new branch, before the unknown-key fallback (§2.3).
3. `_resolve_string` — the port family joins the typed-value preservation (§2.3).
4. The load-time rounds are **unchanged** — they pass no `ports`, so the marker
   survives. **No new round is added.**
5. The use-site uniqueness check (§5).
6. The compose runtime-port check (§2.4).
7. The config builders carry the marker through: `DockerServiceConfig.listen_port:
   int | str`, `ComposeServiceConfig.listen_port: int | str`,
   `UvServiceConfig.listen_port: int | str`, plus `extra_ports`.

### Phase 4 — the option and spec types

`genesis_worker/utils/services/spec.py`, per §2.3 of the ADR. The union, the
constraints on the int member (not the field), `ContainerSpec.listen_port`,
`ContainerSpec.extra_ports`, and the model validator that requires an explicit
`internal_port` when `listen_port` is a marker.

### Phase 5 — the services

`base.py` gains `declared_host_port()`, `effective_host_port()`, the binder
handle, and `own_container_names()`. `docker_service.py`, `compose_service.py`
and `uv_service.py` materialise at start (§3) and read the effective port
everywhere they read `config.listen_port` today. The compose render passes the
port resolver next to the image refs — the same call, one more source.

### Phase 6 — the wiring

- `contracts/ports.py`: the `PortAllocator` `Protocol`.
- `contracts/context.py`: `ServiceContext.port_allocator: PortAllocator | None = None`.
- `contracts/service.py`: `InferenceService.declared_host_port() -> int | None`,
  non-abstract, default `None` — the sanctioned way to extend the contract
  without breaking plugins. Implemented for `llama_swap` (`_port()`),
  `comfyui` (`self._options.listen_port`) and `cptr` (`opts.listen_port`).
- `registries.py`: one allocator per worker (state dir + the fixed-port provider
  + the container-binding probe), passed into every `ServiceContext`; `rebuild`
  reconciles stale records.

The context carries the **handle**; the substitution call decides whether to
**resolve**. That separation is what keeps the allocation at start time without a
second mechanism.

### Phase 7 — the configure panel

A `port` option whose current value is a marker renders as the effective port
(read-only) plus an "assign automatically" checkbox. While the checkbox is ticked,
Apply writes **nothing** for that option, so the YAML default (the marker)
survives; unticking it and applying pins an ordinary fixed port.

This phase depends on the §9 decision.

### Phase 8 — docs and adoption

- `docs/tutorials/declarative-services.md`: the placeholder form, the range, the
  persistence file, the per-kind materialisation points; the "Dynamic port
  discovery — `listen_port` is a fixed integer" limitation bullet goes.
- Adopt the marker in the two specs Gen named — `metube.yaml` (docker) and
  `genesis-productivity.yaml` (compose) — and drop their collision comments.
  **Recommendation: adopt those two only.** A fleet-wide migration of the other
  fourteen moves live ports and breaks anything that hard-codes them (the reverse
  proxy's generated config, bookmarks); that is a separate decision, not a
  side-effect of this design. The uv kind is implemented and tested but not
  adopted (no spec uses it).

---

## 9. The one decision still open: the `write_user_overrides` whole-file wipe

`write_user_overrides` replaces the file; the configure panel writes only the
current service's scalars, so an Apply on one service wipes every other service's
overrides. The new "assign automatically" control needs "write nothing for this
option", which with a whole-file replace means "leave the existing key alone" —
a scoped read-modify-write.

* **Fix here (recommended).** The panel's `_persist` does a read-modify-write
  scoped to the `GENESIS_SERVICES__<NAME>__` prefix: other services' keys and
  non-service keys are preserved. Roughly fifteen lines plus a test. Do it in this
  task because phase 7 depends on it.
* **Own task.** The wipe stays (a pre-existing bug, tracked separately). The
  panel's auto behaviour then works by accident — ticking "auto" drops the pinned
  override for that option (the intended effect) — while the pre-existing wipe of
  other services' overrides remains.

This was never answered (round 2 asked, round 2 was rejected before an answer).
It is re-presented here unchanged.

---

## 10. Open decisions

| # | Decision | Recommendation | Alternative / note |
|---|---|---|---|
| 1 | The `write_user_overrides` whole-file wipe (§9) | **Fix here** — phase 7 depends on it | Own task; the wipe survives as a known bug |
| 2 | The orchestrator's discovery surface (§7) | **Out of scope** — a separate decision in the toolkit repo, as Gen decided | The mechanism already leaves the port observable in three places; the choice needs no change here |
| 3 | Which specs adopt the marker (§8) | **metube + genesis-productivity only** — the two worked examples, one per kind | A fleet-wide migration is a separate decision; it moves live ports |
| 4 | The uv kind in this implementation | **In** — implemented and tested, not adopted | Dropping it leaves "all declarative kinds" half-true; there is no uv spec today |
| 5 | The use-site rule's strictness (§5) | **Reject duplicate names across use sites** | Being lenient (same name = same port, always) would silently give two uses one port — the exact failure the requirement forbids |
| 6 | The compose runtime-port check (§2.4) | **In** — reject a spec whose runtime port is not published | Leaving it out turns a typo into "the service never becomes ready" |

Decisions 1–3 are the ones Gen has to pick. 4–6 are mine, stated so they can be
overturned.

---

## 11. QA

The repo gate, all four, from the repo root:

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

New tests, all hermetic (no docker daemon, no fixed host port, no real bind on a
fixed port — the environment is injected):

* `tests/test_port_markers.py` — the grammar: `is_marker`, `marker_name`,
  `assert_no_markers`; the use-site rule (two options with one name → rejected;
  an option and a reference to it → accepted; a template literal → its own site).
* `tests/test_port_allocator.py` — N distinct ports for N names in one service;
  the same name in two services never gets the same port; a port declared by a
  fake container binding is skipped; a port that fails the bind probe is skipped;
  a port declared by *our own* container is reusable (sticky); the registry file
  round-trips; a second allocator instance (the second worker process) sees the
  first one's allocations; `release` frees a record; the recorded port is reused
  on the next `allocate` and reallocated when it is taken.
* `tests/test_yaml_loader.py` — a spec with `default: "$auto_port.web"` loads and
  the marker survives rounds A/B/C; two markers with the same name in one spec
  raise; `"8096"` from `ctx.options` still validates as the int `8096`;
  `"garbage"` as a default raises; a fixed port still yields the same
  `config.listen_port` as today; the compose runtime-port check fires when the
  template does not publish the probed port.
* `tests/test_docker_service_base.py` — `start()` asks the allocator once per
  marker and passes `int`s to `DockerContainer.run`; two markers produce two
  different ints; the port mapper never sees a marker; `effective_host_port()`
  reports the allocator's record before the first start and `None` when the
  service asked for a port it has never been given.
* `tests/test_compose_service_ports.py` — **Gen's chain A and chain B, end to
  end.** A compose spec whose `options.listen_port` default is `$auto_port.listen`
  and `options.api_port` default is `$auto_port.api` loads; the stored template
  carries both markers; the rendered template carries two *different* allocated
  ints; the rendered file is byte-identical to the pre-change render when the
  options are fixed; the runtime port follows the allocation.
* `tests/test_plugin_boundary.py` — the new `utils/ports` package stays a leaf
  (utils imports nothing outside utils + contracts).
* Existing suites that must stay green unchanged: `test_declarative_specs.py` (all
  16 specs construct and keep their ports), `test_configure_panel.py`,
  `test_compose_stack.py`, `test_api.py` (unchanged — the API did not move).

Manual check on the node, after the implementation lands: convert metube to
`$auto_port.listen`, restart it, confirm `ss -ltn` shows it in the 18000 range,
confirm `docker ps` publishes that port, stop and start it again and confirm the
port is unchanged, then convert genesis-productivity and confirm the rendered
`docker-compose.yml` publishes two different 18000-range ports and that the
health probe still succeeds.

---

## 12. Risks

1. **The pydantic union trap** (Phase 4). A naive `int | str` turns `"8096"` into
   a string and breaks every existing env override; and `Field(default, gt=0,
   lt=65536)` on the union raises a `TypeError` (not a `ValidationError`) on a
   marker, which the loader would not catch. The constraints belong on the int
   member. Pinned by a test.
2. **Two worker processes.** Without `flock` plus a re-read inside the lock, the
   Streamlit process and the API process can hand out the same port. This is the
   failure mode that would look like "random collisions" in production.
3. **The marker's survival depends on the unknown-key fallback.** It is the
   documented behaviour of `_resolve_placeholder` and is exactly how
   `$images.<name>` survives today, but a future "reject unknown placeholders"
   change would break the marker silently. The round-trip test pins it.
4. **Stop/start of a dynamic service changes its port** if the old one was taken.
   Anything hard-coding the port must re-read it. The compose path's stickiness
   depends on the "our own container" exception in the bind test — without it,
   every restart of a running compose stack would move it.
5. **The conservative "declared but stopped" rule** keeps ports unavailable that
   docker would in fact release. Rare, and it prevents a "someone stole my port"
   failure later.
6. **The compose path touches the render path of three live stacks.** Mitigated
   by a golden-file test: the rendered `docker-compose.yml` for a spec with
   fixed options must be byte-identical before and after the change.
7. **`write_user_overrides` wipes the file** (§9). Shipping the "assign
   automatically" control on top of a known wipe ships the bug into a new
   feature.
8. **A plugin that does not implement `declared_host_port()`** does not have its
   port reserved. The bind test still protects it while it is running; the
   framework's own services all implement it.

---

## 13. What I could not finish

* **Nothing is implemented.** This task is design-only per its own text; the
  implementation is the follow-up task.
* **The plan is not in `docs/arch/plans/`** — the repo protocol puts it there
  only after Gen approves the plan. The ADR's Plan section points at
  `plan-043-dynamic-host-port-allocation.md` as "to be written once this ADR is
  accepted".
* **The ADR is Proposed, not Accepted.** It carries Gen's settled decisions
  (§4-§8 of the ADR), the two supersessions (ADR §9 and §13), and the one open
  decision (ADR §11).
* **The orchestrator-side consumer is out of scope** (§7), by Gen's decision.
* **The fleet-wide migration of the remaining fourteen specs is not designed**
  here — §8 recommends against folding it in, and it would need its own look at
  the reverse proxy's generated config.
