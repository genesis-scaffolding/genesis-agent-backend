# ADR-045: Adopt a running container's published host port

## Title

Adopt a running container's published host port — when a service asks for an
auto-allocated host port but the allocation registry has no record for that
use site, the worker records the port the service's *own running container*
already publishes, instead of leaving the use site unallocated forever.

## Status

Accepted (2026-11-06). Task `t-abx64j` (adopt-ports). Companion to ADR-043;
ADR-043 stays Accepted and is annotated, not rewritten (see the annotation in
ADR-043 §8 and its Consequences).

## Context

ADR-043 gave declarative services a `$auto_port.<name>` marker: the framework
allocates a host port from a node-level range at service start, persists the
allocation in `<state_dir>/port_allocations.yaml`, and materialises the integer
into the runtime command.

The mechanism has one precondition it never states: **the record is written by
the start path**. `NodePortAllocator.allocate()` is the only writer of a new
record (`utils/ports/allocator.py:112`), and it is reachable only from
`start()` — through the binder's `resolver()` at `compose_service.py:272`,
`docker_service.py:357` and `uv_service.py:319`. The status path never
allocates; it only reads (`allocated()` → `_read_registry()`, `allocator.py:119-122`).

So a service that was **already running when its spec adopted the marker** has
a running container and no record. `effective_host_port()` returns `None`
(`utils/services/base.py:237-252`), and `status()` takes its `port is None`
branch (`docker_service.py:431-432`, `compose_service.py:293-294`) and reports
`STARTING` with a `null` endpoint — forever. The state cannot change: the only
transition out of that branch depends on a port the framework does not have.
The container is healthy and serving; the framework simply does not know where.

This is exactly what happened on the development node when ADR-043 landed
(`9f2a75b`, 2026-11-05 16:24). Both worker processes restarted at 16:54 with
the new code; the containers for `metube` (host 8098 → container 8081) and
`genesis-productivity` (host 3000 → 3000, host 8765 → 8765) had been started
before the merge and were never restarted under it. The node had no
`port_allocations.yaml` at all — `allocate()` had never run. Both services
reported `starting` with no endpoint, the framework stopped probing them
entirely (`wait_ready()` returns `False` immediately when the port is `None`),
and the dashboard showed no Web UI link for either.

The gap in ADR-043 is that its failure-mode table collapses two different
situations into one row:

> | The service has never started | The effective port is `None`. `status()`
> and `web_ui_endpoint()` report `None` rather than a wrong number. |

"The service has never started" is the expected outcome of a service that has
not run yet. "The service is running and was started before the spec adopted
the marker" is a different situation with the same observable state, and the
design only distinguished the first. ADR-043 §8 already states the principle
the fix follows —

> The registry file is the record; the running container is the truth.

— but the implementation only wrote the record from the start path, so a
container that outlived its spec's migration had no way back into the record.

The alternatives were considered and rejected:

- **Restart the two services under the new code** (the ports move to the
  18000 range). It works, but it moves three ports that two genesis CLI token
  stores (`~/.pi/genesis-tokens.json` in two working directories, both
  pointing at `http://p3:8765`) and a set of bookmarks depend on, and it gives
  up the collision protection ADR-043 was written to buy for exactly these
  services.
- **Backfill the registry file by hand** (a one-time data fix). It fixes today
  and does nothing about the next spec migration.
- **Say `FAILED` instead of `STARTING`.** More alarming, less informative, and
  still not a fix.

What is missing is the structural piece: a way for the worker to learn the port
a running container already publishes, and to record it.

## Decision

We will let the worker **adopt** the host port a service's own running
container already publishes, when that service asks for an auto-allocated port
and the registry has no record for the use site.

### 1. The allocator gains an `adopt()` primitive

`NodePortAllocator.adopt(service, name, port) -> bool` records a port that the
service's own container already publishes. It is the read-side sibling of
`allocate()` and obeys the same rules:

- it runs inside the same exclusive `flock` (`allocator.py:198-208`) and
  re-reads the registry **inside** the lock, so the two worker processes cannot
  race (ADR-043 §4);
- it is **idempotent**: a use site that already has a record keeps it and
  `adopt()` returns `False` — adoption never overwrites a live allocation;
- it **refuses** a port another service holds: another service's record, or a
  port in the reserved set (the framework's own ports plus every registered
  service's declared fixed port);
- it does **not** run the bind probe. The port is not a candidate — it is
  already published by our own container, which is precisely the exception
  ADR-043 §5 rule 4 makes for our own containers.

`PortAllocator` (`contracts/ports.py`) gains the method, and `PortBinder`
gains a thin `adopt(name, port)` so callers work through the per-service
handle rather than the node-level allocator.

### 2. Every declarative service can report its published host ports

`DeclarativeServiceBase.published_host_ports() -> Mapping[str, int]` answers:
*which host port does this service's own running container already publish, for
each of my auto-port use sites?*

- **docker kind** — the mapping is structural, no parsing: the primary use site
  is `container.internal_port`, each extra use site is its
  `container.extra_ports[].container_port`. The host port comes from the
  container's own `HostConfig.PortBindings` (`{"8081/tcp": [{"HostPort":
  "8098"}]}`).
- **compose kind** — the marker-to-container-port mapping lives in the template
  text, so the loaded template is parsed: each `ports:` entry is split into its
  host port and its container port, an entry whose host port is a marker
  yields `marker -> container_port`, and the container port is looked up in the
  project's container bindings. A template that does not parse, or a ports
  entry with no container port, yields **no** observation for that use site.

Only **running** containers are observed: a stopped container declares its
bindings but does not hold the port, so adopting its declared port would
record something untrue.

### 3. The registry adopts at construction and after a rebuild

`ServiceRegistry` runs the adoption pass at the end of `__init__` (after the
specs are loaded, so every service is known) and at the end of `rebuild` (for
the rebuilt service). For each declarative service that declares auto-port use
sites, for each use site with no record, the pass adopts the published port.

Both worker processes run this at startup. The `flock` plus the
read-inside-the-lock rule makes the double run harmless: the first process
writes the record, the second one sees it and skips.

### 4. The status branch explains itself

The `port is None` branch of `status()` (docker, compose, uv) stops reporting
a bare `STARTING`: it carries a message naming the marker and saying that no
host port is recorded for it. The next occurrence is self-explaining instead of
a half-day investigation.

### 5. `genesis-productivity` is pinned to fixed ports

The productivity stack's two host ports (3000 for the Streamlit UI, 8765 for
the FastAPI server) are **declared** again rather than asked for. Two working
directories' genesis CLI token stores and a set of bookmarks point at
`http://p3:8765`; the framework must not be free to move that port. The
service's container is not restarted — the spec change alone makes
`effective_host_port()` return 3000 again, so the status probe runs against the
port that is actually serving.

`metube` and every other spec keep the `$auto_port` markers. Auto-assignment
stays the framework behaviour for the services that do not pin their ports.

## Consequences

**Positive**

- A spec migration no longer strands a running service: the worker adopts the
  port the container already publishes, and the service keeps reporting
  `running` with the endpoint it always had.
- The adoption makes the *next* start sticky: the adopted record is a recorded
  port, so the allocator's sticky rule reuses it (the own-container free check
  passes, because our own container publishes it).
- No container is recreated and no client moves. The fix is a record, not a
  restart.
- The registry file stays the single source of truth for allocations: the
  adopted port is an ordinary record, read by `allocated()` like any other.

**Negative**

- The compose kind needs a template parse (YAML + a ports-entry split). It is
  the awkward half of this ADR, and it is best-effort: a template that does not
  parse, or a ports entry without a container port, yields no observation and
  the use site stays unallocated.
- The adoption pass reads the docker daemon at worker startup (one
  `docker ps -aq` plus one `docker inspect` per container). It is skipped
  entirely when no registered service declares an auto-port use site.
- The adoption pass writes to the node's state directory at startup. The
  registry therefore takes an explicit `adopt_published_ports` switch so the
  test suite does not mutate a developer's live state.
- `adopt()` refuses a port another service holds, so a genuine conflict on the
  node (two containers publishing the same host port) is not recorded by
  either — the second service stays unallocated rather than lying about the
  conflict.

**Neutral**

- The reserved set still covers only a service's *primary* declared port
  (`declared_host_port()`), so the pinned stack's second port (8765) is not
  reserved by the framework. It is protected by the container-binding rule
  (ADR-043 §5 rule 3) while the container exists, and it is outside the
  dynamic range, so the allocator cannot hand it out today.
- `ServiceState.FAILED` was considered for the unrecorded-port branch and
  rejected: `STARTING` with an explanation is the smaller, less alarming
  change.

## Plan

`docs/arch/plans/plan-045-adopt-published-host-ports.md` — file-by-file
execution in five phases.
