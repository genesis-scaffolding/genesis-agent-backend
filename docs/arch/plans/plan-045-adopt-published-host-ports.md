# Plan — adopt a running container's published host port (ADR-045) and pin `genesis-productivity` to 3000/8765

> **Approved and implemented.** Gen approved this plan as-is on 2026-11-06.
> Task `t-abx64j` (adopt-ports) implemented the five phases below against this
> plan; the ADR landed in the same PR as the code it describes
> (`adr-045-adopt-published-host-ports.md`).

Task: `t-abx64j` — *Adopt running services' published host ports (ADR-043 stuck-services fix) and pin genesis-productivity to 3000/8765*.
Worktree (write-scope): `/home/gentran/Documents/genesis-agent-backend/worktrees/t-abx64j-adopt-ports/genesis-agent-backend`
Branch: `se/t-abx64j-adopt-ports` (from `main` @ `a00dbb4`).
ADR: `docs/arch/adr-045-adopt-published-host-ports.md` (landed with the code).

Authoritative inputs (read first, obeyed as given):

- `/tmp/auto-port-root-cause-report.md` — the root-cause investigation.
- `/tmp/auto-port-fix-brief.md` — Gen's verbatim design direction (authoritative; not re-litigated here).

---

## 1. Goal

Two services on this node (`metube`, `genesis-productivity`) report `starting`
with a null endpoint and will never leave that state: their containers were
started before their specs adopted the `$auto_port` marker, so the allocation
registry has no record for them, and the record is only ever written by the
**start** path.

After this task:

1. **`genesis-productivity` is pinned** to fixed `3000` (Streamlit UI) and
   `8765` (FastAPI). Its container is **never restarted**. After the deploy its
   status is correct immediately, because the spec declares the ports the
   container already publishes.
2. **`metube` keeps the `$auto_port` marker**, and its running container's
   published port (`8098`) is **adopted** by the worker — no hand-edit of the
   registry, no restart. A later restart of metube keeps `8098`.
3. The **structural fix** lands: the next spec migration adopts a running
   service's published port instead of stranding it, and the "no port
   recorded" status explains itself instead of lying.
4. The ADR-043 gap is closed by a new ADR (§4.6).

Non-goals: moving any port, restarting any container, changing the dynamic
range, migrating the other fourteen specs to markers, and any HTTP API change.

---

## 2. The pinned-ports decision (genesis-productivity only)

Gen's direction, verbatim from the brief:

> "For the genesis-productivity service, please remove the auto port and use the
> correct port 3000 for ui, 8765 for api. For the other we can still use auto
> port no problem."

**What changes** — `genesis_worker/services/_declarative/genesis-productivity.yaml`,
two option defaults, reverted to what they were before ADR-043 (`9f2a75b`):

```yaml
  listen_port:
    type: port
    default: 3000          # was "$auto_port.listen"
    ui_help: Host-side port for the Streamlit UI     # marker wording dropped
  api_port:
    type: port
    default: 8765          # was "$auto_port.api"
    ui_help: Host-side port for the FastAPI server (CLI/agent access)
```

The header comment ("Both host ports are allocated by the framework …") is
replaced with a short note saying the two ports are **pinned** because clients
hard-code them (the genesis CLI token stores in two working directories point
at `http://p3:8765`; the Streamlit UI is bookmarked at `:3000`).

**What does not change** — `metube.yaml` and every other spec keep their
`$auto_port` markers. The dynamic range, the allocator and the marker grammar
are untouched.

**Why the pin is safe (verified against the live node):**

| Fact | Verified how |
|---|---|
| The running container publishes `3000→3000` and `8765→8765` | `docker inspect genesis-productivity` → `HostConfig.PortBindings` |
| The rendered compose file with the pinned spec is byte-identical to the file on disk | rendered the pinned spec in-process; the ports lines are `"0.0.0.0:3000:3000"` / `"0.0.0.0:8765:8765"`, same as `~/.local/state/genesis-worker/genesis-productivity/compose/docker-compose.yml` |
| With the pin, `declared_host_port()` = 3000, `effective_host_port()` = 3000, `declared_port_markers()` = `()` | loaded the pinned spec in-process |
| The status probe hits the port that is actually serving | `http://127.0.0.1:3000/` → 200; the health probe path is `/` and Streamlit answers it |
| The container is not recreated | the container is not touched by this task; the deploy restarts the *worker*, not the container |

**Consequence for the allocator's reserved set** (asked for explicitly):
`ServiceRegistry._reserved_ports()` collects every registered service's
`declared_host_port()`. With the pin, **3000 joins the reserved set** (it is
genesis-productivity's primary declared port). **8765 does not** — the reserved
set covers only a service's *primary* declared port, and `api_port` is not
`listen_port`. That is harmless today: the dynamic range is `18000-18999`, so
the allocator can never hand out 3000 or 8765, and 8765 is additionally
protected by ADR-043 §5 rule 3 (a container's *declared* bindings are reserved)
for as long as the container exists. The same shape already exists for
`genesis-agent-context` (`3001` + `8766`), so this is a pre-existing gap in the
reserved set, not one this task introduces. It is listed in §8 (risks) and
deliberately **not** fixed here (fixing it means widening the
`declared_host_port()` contract to a plural form — a separate decision).

---

## 3. What the investigation confirmed (the report's chain)

Every file:line below was re-read in the worktree; the chain holds exactly as
the report states.

| # | Link | Where |
|---|---|---|
| 1 | the spec's port option default is the marker | `services/_declarative/metube.yaml:66`, `genesis-productivity.yaml:103` and `:109` |
| 2 | the load-time rounds resolve no marker | `utils/services/loader.py:587` (A), `:594` (B), `:604` (C) — none passes `ports=` |
| 3 | the auto-port branch returns the marker untouched | `loader.py:244-247` |
| 4 | the marker names are collected as use sites | `loader.py:73-115`, `:612` |
| 5 | `effective_host_port()` resolves the marker through the registry | `utils/services/base.py:237-252` |
| 6 | `allocated()` is a read-only lookup; a missing file reads as `{}` | `utils/ports/binder.py:40-44` → `utils/ports/allocator.py:119-122` → `:210-235` |
| 7 | **`allocate()` is the only writer of a new record** | `allocator.py:112`; `release()` `:124` and `reconcile()` `:135` only delete |
| 8 | `allocate()` is reachable only from the start path | `binder.py:68-72` → `compose_service.py:272`, `docker_service.py:357`, `uv_service.py:319` |
| 9 | the registry file does not exist on this node | `~/.local/state/genesis-worker/` holds no `port_allocations.yaml` and no `port_allocations.lock` |
| 10 | `status()` takes the `port is None` branch | `docker_service.py:431-432`, `compose_service.py:293-294` (and `uv_service.py:355-356`) |
| 11 | the API surfaces it | `api/routes/services.py:37-40`, `:52-53` |

Live state re-confirmed during the investigation:

```
genesis-productivity  Up 46 hours (healthy)  0.0.0.0:3000->3000/tcp, 0.0.0.0:8765->8765/tcp
metube                Up 7 days (healthy)    0.0.0.0:8098->8081/tcp
~/.local/state/genesis-worker/port_allocations.yaml   -> does not exist
```

The container binding shapes the fix depends on (from `docker inspect`):

```
metube              HostConfig.PortBindings = {"8081/tcp":[{"HostIp":"0.0.0.0","HostPort":"8098"}]}
                     Config.Labels           = {}
genesis-productivity HostConfig.PortBindings = {"3000/tcp":[{...,"HostPort":"3000"}],
                                                 "8765/tcp":[{...,"HostPort":"8765"}]}
                     Config.Labels["com.docker.compose.project"] = "genesis-productivity-stack"
```

So: the docker kind's use site maps to a **container port** (`8081` → host
`8098`); the compose kind's use sites map to container ports through the
**template text** (`$auto_port.listen` → container `3000` → host `3000`;
`$auto_port.api` → container `8765` → host `8765`).

The compose template parse was validated against the real, loaded spec:

```
loaded template ports entries: ['0.0.0.0:$auto_port.listen:3000', '0.0.0.0:$auto_port.api:8765']
yaml.safe_load(template)      -> works; the inline comments are stripped by the parser
```

---

## 4. The approach

### 4.1 `NodePortAllocator.adopt(service, name, port) -> bool`

A new primitive in `genesis_worker/utils/ports/allocator.py`, next to
`allocate()` / `release()` / `reconcile()`:

```python
def adopt(self, service: str, name: str, port: int) -> bool:
    """Record the port ``service``'s own container already publishes.

    Idempotent: a use site that already has a record keeps it.
    Refuses a port another service holds.
    """
    with self._locked():
        registry = self._read_registry()
        if registry.get(service, {}).get(name) is not None:
            return False                                    # never overwrite
        for other_service, records in registry.items():
            for other_name, other_port in records.items():
                if other_port != port:
                    continue
                if other_service == service and other_name == name:
                    continue
                return False                                # another service holds it
        if port in set(self._fixed_ports()):
            return False                                    # a reserved fixed port
        registry.setdefault(service, {})[name] = port
        self._write_registry(registry)
        return True
```

Rules, and why each one is there:

| Rule | Reason |
|---|---|
| same `flock` + **re-read inside the lock** | ADR-043 §4's two-process rule; the two worker processes both run the adoption pass at startup |
| **idempotent** — an existing record is never overwritten | the pass runs in two processes and on every rebuild; a live allocation must not be clobbered by an observation |
| **refuses another service's record** | two services must never hold the same port; the second one stays unallocated rather than lying |
| **refuses a reserved fixed port** | `self._fixed_ports()` is the registry's `_reserved_ports()` (framework ports + every registered service's declared fixed port). A container of ours publishing a port another service *declares* is a node-level conflict; recording it would make the conflict permanent |
| **no bind probe, no range walk** | the port is not a candidate — it is *already published by our own container*. This is exactly the exception ADR-043 §5 rule 4 makes for our own containers (the port is "free for us" because we hold it) |
| the write is the existing atomic write | sibling `.tmp` + `os.replace`, mode `0600` (`allocator.py:_write_registry`) |

`contracts/ports.py` — the `PortAllocator` protocol gains:

```python
def adopt(self, service: str, name: str, port: int) -> bool:
    """Record a port the service's own running container already publishes."""
    ...
```

`utils/ports/binder.py` — `PortBinder.adopt(name, port) -> bool` delegates to
the allocator, so callers work through the per-service handle (the binder
already knows the service name) rather than the node-level allocator.

### 4.2 `published_host_ports()` — the per-service observation

New method on `DeclarativeServiceBase` (`utils/services/base.py`):

```python
def published_host_ports(self, bindings: Sequence[ContainerPortBinding] = ()) -> Mapping[str, int]:
    """The host ports this service's own running container already publishes.

    Keyed by auto-port use-site name. Empty for a service that publishes
    nothing, is not running, or has no auto-port use site.
    """
    return {}
```

`bindings` is the snapshot of every container's port bindings on the node; an
empty argument means "probe it yourself" (the override calls
`container_port_bindings()` in that case). The registry passes **one** snapshot
to every service, so the daemon is read once per adoption pass, not once per
service.

**The probe has to carry two facts the current one throws away.**
`utils/process/docker.py`'s `container_port_bindings()` keeps only the *set* of
host ports per container; the adoption needs the **container-port → host-port
pairing** and the **running** state. So `ContainerPortBinding` grows two
fields, filled from the same `docker inspect` output that is already parsed
(no extra subprocess):

```python
@dataclass(frozen=True)
class ContainerPortBinding:
    name: str
    host_ports: tuple[int, ...]
    compose_project: str | None = None
    running: bool = False                                  # NEW
    port_map: tuple[tuple[int, int], ...] = ()             # NEW: (container_port, host_port)
```

`running` comes from `State.Running`; `port_map` from the same
`HostConfig.PortBindings` dict (key `"8081/tcp"` → container port `8081`). When
a container port has more than one binding, the first one wins.

**docker kind** (`utils/services/docker_service.py`) — the use-site → container
port mapping is **structural, no parsing**:

```python
def published_host_ports(self, bindings=()):
    own = [b for b in (bindings or container_port_bindings())
           if b.name == self.config.container_name and b.running]
    if not own:
        return {}
    ports = dict(own[0].port_map)
    published: dict[str, int] = {}
    marker = marker_name(self.config.listen_port)          # the primary use site
    if marker is not None and self.config.internal_port is not None:
        host = ports.get(self.config.internal_port)
        if host is not None:
            published[marker] = host
    for extra in self.config.extra_ports:                  # the extra use sites
        name = marker_name(extra.host)
        if name is None:
            continue
        host = ports.get(extra.container_port)
        if host is not None:
            published[name] = host
    return published
```

For metube: `config.listen_port` = `"$auto_port.listen"`, `internal_port` =
`8081`, the container publishes `8081/tcp → 8098` → `{"listen": 8098}`.

**compose kind** (`utils/services/compose_service.py`) — the use-site →
container-port mapping lives in the **template text**, so the loaded template is
parsed. The parse is *best-effort and total*: anything it cannot read yields
**no observation** (never a wrong one).

```python
def published_host_ports(self, bindings=()):
    own = [b for b in (bindings or container_port_bindings())
           if b.compose_project == self._project_name and b.running]
    if not own:
        return {}
    ports: dict[int, int] = {}
    for b in own:
        ports.update(b.port_map)
    published: dict[str, int] = {}
    for marker, container_port in self._template_marker_container_ports().items():
        host = ports.get(container_port)
        if host is not None:
            published[marker] = host
    return published
```

The template parse — **the awkward part**, designed concretely:

```python
def _template_marker_container_ports(self) -> Mapping[str, int]:
    """marker name -> container port, read from the template's ``ports:`` entries."""
    try:
        doc = yaml.safe_load(self.config.compose_template)
    except yaml.YAMLError:
        return {}                       # an unparseable template observes nothing
    if not isinstance(doc, dict):
        return {}
    services = doc.get("services")
    if not isinstance(services, dict):
        return {}
    out: dict[str, int] = {}
    for spec in services.values():
        if not isinstance(spec, dict):
            continue
        entries = spec.get("ports")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, str):
                continue
            parsed = _split_ports_entry(entry)
            if parsed is None:
                continue
            host_port, container_port = parsed
            name = marker_name(host_port)
            if name is None:
                continue
            out.setdefault(name, container_port)
    return out
```

with the entry splitter:

```python
def _split_ports_entry(entry: str) -> tuple[str, int] | None:
    """(host_port, container_port) of a compose ``ports:`` entry, or None.

    Docker's port spec is ``[HOST_IP:][HOST:]CONTAINER[/PROTOCOL]`` — the
    container port is always last, the host port is the field before it. An
    entry with only a container port publishes no host port at all, so there
    is nothing to observe.
    """
    text = entry.strip()
    if text.startswith("["):                      # bracketed IPv6 host address
        close = text.find("]")
        if close == -1 or not text[close + 1:].startswith(":"):
            return None
        text = text[close + 2:]
    parts = text.split(":")
    if len(parts) < 2:
        return None
    host_port, container_port = parts[-2], parts[-1]
    container_port = container_port.split("/")[0]
    if not container_port.isdigit():
        return None
    return host_port, int(container_port)
```

Why YAML-parse rather than regex the template text: the ports entries are
quoted strings in the template, the inline comments (`# Streamlit UI`) are
stripped by the parser, and the structure (`services: → <name>: → ports:`) is
found without caring about indentation. A regex would have to re-implement
YAML. The YAML parse is wrapped in `try/except yaml.YAMLError` — a template
that does not parse observes nothing rather than breaking the worker.

**Robustness matrix for the compose parse** (what happens when…):

| Template situation | Result |
|---|---|
| `"0.0.0.0:$auto_port.listen:3000"` | `listen → 3000` ✓ |
| `"$auto_port.api:8765"` (two-field form) | `api → 8765` ✓ (container port is always last) |
| `"3000"` (container port only) | **no observation** — no host port is published |
| `"0.0.0.0:$auto_port.listen"` (no container port) | **no observation** — the container port cannot be mapped |
| `"0.0.0.0:8080:8080"` (no marker) | not a use site — skipped |
| the template does not parse as YAML | **no observation** — `yaml.YAMLError` caught |
| the container for the project is not running | **no observation** — a stopped container declares its bindings but does not hold the port |
| the container publishes a different container port than the template says | **no observation** — the lookup misses; no wrong record |

### 4.3 The adoption call sites

`ServiceRegistry` (`genesis_worker/registries.py`):

```python
def __init__(self, settings: Settings, *, adopt_published_ports: bool = True) -> None:
    ...
    self._enabled: set[str] = self._load_or_bootstrap_enabled_set()
    if adopt_published_ports:
        self._adopt_published_ports()


def _adopt_published_ports(self, services: Iterable[InferenceService] | None = None) -> None:
    """Record the host ports our running containers already publish (ADR-045).

    A service that was running when its spec adopted the marker has no
    allocation record — the record is only written on the start path — and
    would report ``starting`` with no endpoint forever. The running
    container is the truth: its published port is adopted.
    """
    targets = [
        svc
        for svc in (services if services is not None else self._instances.values())
        if isinstance(svc, DeclarativeServiceBase) and svc.declared_port_markers()
    ]
    if not targets:
        return
    bindings = container_port_bindings()          # one snapshot for every service
    for svc in targets:
        for marker, port in svc.published_host_ports(bindings).items():
            try:
                svc.port_binder().adopt(marker, port)
            except OSError:
                continue                           # a read-only state dir must not stop the worker
```

`rebuild(name)` gains the same call for the rebuilt service, **after** the
existing `reconcile` (reconcile drops stale records first; adoption then records
what the rebuilt service publishes):

```python
        self._port_allocator.reconcile(name, new_svc.declared_port_markers())
        if self._adopt_published_ports_enabled:
            self._adopt_published_ports([new_svc])
```

Notes:

- **Both worker processes run this at startup.** The `flock` plus the
  read-inside-the-lock rule makes the double run harmless: the first process
  writes the record, the second sees it and skips (adoption is idempotent).
- The pass is **inert when no registered service declares an auto-port use
  site** — no docker call, no write. With the pin in place, metube is the only
  marker service on this node.
- The pass **only observes running containers** (see §4.2), so a stopped
  container's declared-but-unheld port is never recorded.

### 4.4 The status branch explains itself

The `port is None` branch of `status()` currently returns a bare `STARTING`.
It gets a message (docker `docker_service.py:431-432`, compose
`compose_service.py:293-294`, uv `uv_service.py:355-356`):

```python
if port is None:
    return ServiceStatus(
        state=ServiceState.STARTING,
        endpoint=endpoint,
        message=self.unrecorded_port_message(),
    )
```

with one shared helper on the base class:

```python
def unrecorded_port_message(self) -> str:
    """Why the effective port is unknown while the container is up."""
    marker = marker_name(getattr(self.config, "listen_port", None))
    ref = f"$auto_port.{marker}" if marker else "the auto-port marker"
    return (
        f"the container is running but no host port is recorded for {ref} — "
        "the worker could not map the container's published port to this use "
        "site (the service may have been started before its spec adopted the "
        "marker); restart the service to allocate one"
    )
```

`ServiceStatus.message` already exists (`contracts/service.py:65-70`) and
already reaches the API (`api/schemas.py:146`, `:156`), so the explanation
shows up in `GET /v1/services` with **no API change** (ADR-043 §13's rule:
the port-assignment mechanism stays internal).

The uv kind is included because it is the same branch and the same hole; no
uv spec exists today, so the change is one line.

### 4.5 Order of work (implementation phases)

1. **Phase 1 — the primitive.** `adopt()` in the allocator + the protocol +
   the binder, with the allocator-level tests.
2. **Phase 2 — the observation.** `ContainerPortBinding` gains `running` and
   `port_map`; `published_host_ports()` on the base class, the docker kind and
   the compose kind (including the template parse), with the kind-level tests.
3. **Phase 3 — the pass.** The registry's adoption pass, the `__init__` and
   `rebuild` call sites, the `adopt_published_ports` switch, with the
   registry-level and migration-level tests.
4. **Phase 4 — the message.** The `port is None` status branch (docker,
   compose, uv) + the base-class helper.
5. **Phase 5 — the pin + the docs.** `genesis-productivity.yaml` back to
   `3000`/`8765`; the tutorial's registry example; the ADR-043 annotation.

### 4.6 The ADR

**Decision: a new ADR — `ADR-045: Adopt a running container's published host
port`** (written to `docs/arch/adr-045-adopt-published-host-ports.md` as part of
this plan; it lands in the same PR as the code it describes).

*Why a new ADR rather than an amendment of ADR-043:*

- The change **changes a contract**: `PortAllocator` (`contracts/ports.py`)
  gains a method, and the declarative base gains an observation method that the
  framework calls at construction time. The repo's own bar (AGENTS.md): "changes
  that cross the framework/plugin boundary, change a contract, introduce a new
  pattern".
- It **introduces a new pattern** (observe-then-adopt at registry construction)
  that ADR-043 did not describe, and it had **multiple valid alternatives with
  real trade-offs** (the report's options a–d: adopt in code / restart the
  services / backfill the file by hand / stop lying about the state).
- ADR-043 is Accepted and shipped; rewriting its text would blur what was
  decided on 2026-11-04 with what is decided now. The repo's convention for a
  superseded section is an **inline annotation** (AGENTS.md, "Superseded"), not
  a rewrite — so ADR-043 gets an annotation, and ADR-045 gets the decision.

**ADR-043's gap is closed by an inline annotation in ADR-043** (the merge chore
in §6), at the two places the gap is visible:

- §8, at the sentence *"The registry file is the record; the running container
  is the truth."* → an "Amended by ADR-045" note pointing at the adoption pass.
- Consequences, at the negative bullet *"A service that asks for a port has no
  endpoint until it has started once…"* → the same pointer, noting that
  "started once" now includes "adopted once".

The optional third annotation — the failure-mode row in
`docs/arch/plans/plan-043-dynamic-host-port-allocation.md` §4.5 ("The service
has never started") — is listed in the merge chore as optional; the plan
documents are historical records of what was approved.

---

## 5. Files and areas touched

### New

| File | Contents |
|---|---|
| `docs/arch/adr-045-adopt-published-host-ports.md` | the ADR (landed with the code) |
| `docs/arch/plans/plan-045-adopt-published-host-ports.md` | this plan |
| `genesis_worker/tests/test_port_adoption.py` | the migration-level tests (the brief's scenario, the registry pass, the rebuild path, the switch) |

### Modified

| File | Change |
|---|---|
| `genesis_worker/utils/ports/allocator.py` | `adopt(service, name, port) -> bool` — flock, read-inside-the-lock, idempotent, refuses another service's port and a reserved port, no bind probe |
| `genesis_worker/contracts/ports.py` | the `PortAllocator` protocol gains `adopt` |
| `genesis_worker/utils/ports/binder.py` | `PortBinder.adopt(name, port)` — the per-service handle |
| `genesis_worker/utils/process/docker.py` | `ContainerPortBinding` gains `running` and `port_map`; `_binding_from_inspect` fills them from the same `docker inspect` output |
| `genesis_worker/utils/services/base.py` | `published_host_ports()` (base, returns `{}`) and `unrecorded_port_message()` |
| `genesis_worker/utils/services/docker_service.py` | the docker override of `published_host_ports()`; the status message on the `port is None` branch |
| `genesis_worker/utils/services/compose_service.py` | the compose override of `published_host_ports()`; `_template_marker_container_ports()`; `_split_ports_entry()`; the status message |
| `genesis_worker/utils/services/uv_service.py` | the status message on the `port is None` branch (one line, same hole) |
| `genesis_worker/registries.py` | `ServiceRegistry.__init__(..., adopt_published_ports: bool = True)`; `_adopt_published_ports()`; the `rebuild` call site |
| `genesis_worker/services/_declarative/genesis-productivity.yaml` | the two option defaults back to `3000` / `8765`; the header comment |
| `docs/arch/adr-043-dynamic-host-port-allocation.md` | the inline annotation pointing at ADR-045 (§8 + Consequences) |
| `docs/tutorials/declarative-services.md` | the registry example (drop genesis-productivity from it); a short paragraph on adoption |
| `genesis_worker/tests/test_port_allocator.py` | the `adopt()` tests |
| `genesis_worker/tests/test_docker_service_base.py` | the docker observation tests + the status-message test |
| `genesis_worker/tests/test_compose_service_ports.py` | the template-parse tests + the compose observation tests |
| `genesis_worker/tests/test_declarative_specs.py` | `test_genesis_productivity_asks_for_two_ports` → the pinned expectations; the adopters' adoption test |
| `genesis_worker/tests/test_services_registry.py` | the bare-`Settings()` constructions get `adopt_published_ports=False` (see §8 risk 4) |
| `genesis_worker/tests/conftest.py` | the docker-binding probe is stubbed for the suite (the adoption pass must not shell out) |

### Untouched on purpose

- `genesis_worker/api/**` — no API change (ADR-043 §13's rule).
- `genesis_worker/utils/services/loader.py` — the marker grammar, the rounds and
  the use-site rule do not change.
- `genesis_worker/utils/services/configure_panel.py` — a pinned `port` option
  renders as the ordinary number input; a marker option renders as before.
- `genesis_worker/services/_declarative/metube.yaml` and the other thirteen
  specs — the markers stay.
- The dynamic range, `framework_ports()`, the bind probe.

---

## 6. Repo workstreams

**One repo. One workstream. One PR. One gate.**

| | |
|---|---|
| **Repo** | `genesis-agent-backend` |
| **Worktree (write-scope)** | `/home/gentran/Documents/genesis-agent-backend/worktrees/t-abx64j-adopt-ports/genesis-agent-backend` |
| **Branch** | `se/t-abx64j-adopt-ports` |
| **QA gate** | the four commands in §7.1 (pytest, pyright, ruff check, ruff format --check) |
| **Landing** | merge the branch into `main` with `git merge --no-ff`, then `git push` (the repo's own workflow, AGENTS.md) |
| **PR** | one PR for the whole workstream |

**Landing order (single workstream):**

1. Implementation lands on `se/t-abx64j-adopt-ports`; the QA gate passes.
2. Review → PR → approval → merge to `main` → push.
3. **Deploy: genesis-agent-backend to p3 (human)** — a named human step, *not* a
   stage. The coordinator records it as a gate line; it is never executed by the
   pipeline.

**What the deploy does, and what it does not do (this is the acceptance
criterion):**

| After the deploy (worker restart) | Expected |
|---|---|
| `genesis-productivity` | reports **running**, `web_ui_endpoint` = `http://p3:3000/`, API still on `8765`. **No restart, no recreate** — the container is untouched. |
| `metube` | reports **running**, `web_ui_endpoint` = `http://p3:8098/`. The adoption pass recorded `metube.listen = 8098` **by itself** — no hand-edit of the registry file. |
| `~/.local/state/genesis-worker/port_allocations.yaml` | now exists, containing `metube: {listen: 8098}` |
| `metube` (optional, afterwards) | may be restarted; it comes back on **8098** (the recorded candidate passes the own-container free check, ADR-043 §5 rule 4) |
| `genesis-productivity` | is **never** restarted by this task |

**Merge chores (declared, not discovered):**

1. **ADR-045 lands with the branch.** It is written in this task's worktree
   (`docs/arch/adr-045-adopt-published-host-ports.md`); it must be merged in
   the same PR as the code it describes.
2. **ADR number claim.** `045` is claimed by this task (044 is the latest
   existing ADR). If another branch claims `045` first, this ADR is renumbered
   at merge time per the repo's renumbering queue (the ADR-043/ADR-044
   precedent). *Merge chore: renumber if the queue moved.*
3. **ADR-043 annotation.** The inline "Amended by ADR-045" notes in ADR-043
   §8 and Consequences are part of this same merge — they are the closure of the
   ADR gap and must not be left for a later PR. *Merge chore: the
   annotation is in the same PR as ADR-045.*
4. *(Optional)* the failure-mode row in `docs/arch/plans/plan-043-…md` §4.5
   gets the same pointer, in the same merge.

---

## 7. QA plan

### 7.1 The in-repo gate (all four, from the repo root)

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

(`make test` is a thin wrapper around the first three; AGENTS.md requires all
four.) **Baseline measured at the plan stage (`703a24e`):**

```
uv run pytest -q              -> 1436 passed, 37 deselected, 1 warning in 41.30s
uv run pyright                -> 0 errors, 0 warnings, 0 informations
uv run ruff check genesis_worker      -> All checks passed!
uv run ruff format --check genesis_worker -> 225 files already formatted
```

### 7.2 New tests (the report's regression plan, adapted to what this plan implements)

**`genesis_worker/tests/test_port_allocator.py`** — the primitive:

- `test_adopt_records_a_published_port` — `adopt("metube", "listen", 8098)`;
  `allocated(...) == 8098`, the YAML file holds it, `snapshot()` shows it.
- `test_adopt_does_not_overwrite_an_existing_record` — adopt 8098, adopt 9000 →
  the record stays 8098.
- `test_adopt_refuses_a_port_another_service_holds` — service A allocated
  18000; adopt 18000 for service B → B has no record.
- `test_adopt_refuses_a_reserved_fixed_port` — 8098 is in the fixed-port set →
  no record.
- `test_adopt_is_visible_to_a_second_allocator` — the second worker process
  reads the adopted port.
- `test_adopting_twice_is_idempotent` — two adopts leave one record and raise
  nothing (the two worker processes both adopt at startup).
- `test_adopt_does_not_run_the_bind_probe` — the port is already published by
  our own container; the bind probe is never consulted.

**`genesis_worker/tests/test_docker_service_base.py`** — the docker kind:

- `test_published_host_ports_reads_the_container_bindings` — a stubbed binding
  snapshot (`{"8081/tcp": 8098}`, running) → `{"listen": 8098}`.
- `test_a_stopped_container_publishes_nothing` — the same snapshot with
  `running=False` → `{}`.
- `test_status_is_running_when_the_container_publishes_an_unrecorded_port` —
  marker default, empty registry, container stubbed running and publishing
  8098, after the adoption: `effective_host_port() == 8098`,
  `status().state == RUNNING`, `status().endpoint == "http://127.0.0.1:8098/"`,
  `web_ui_endpoint()` ends with `":8098/"`.
- `test_the_adopted_port_survives_into_the_next_process` — a second allocator
  over the same state dir sees the record, and `allocate("listen")` returns
  8098, not 18000.
- `test_a_service_with_no_published_port_is_not_adopted` — nothing published →
  no record, and the status carries the explanatory message.
- `test_the_status_message_names_the_missing_marker` — the `port is None`
  branch's message names `$auto_port.listen`.

**`genesis_worker/tests/test_compose_service_ports.py`** — the compose kind:

- `test_the_template_parse_maps_a_marker_to_its_container_port` —
  `listen → 3000`, `api → 8765`.
- `test_a_ports_entry_without_a_container_port_yields_no_observation`.
- `test_a_ports_entry_without_a_marker_is_not_a_use_site`.
- `test_a_template_that_does_not_parse_yields_no_observation`.
- `test_published_host_ports_reads_the_projects_container` — a stubbed project
  binding (`3000/tcp → 3000`, `8765/tcp → 8765`, running) → `{"listen": 3000,
  "api": 8765}`.
- `test_status_is_running_for_a_running_stack_with_unrecorded_ports` — after
  adoption: `effective_host_port() == 3000`, `status().state == RUNNING`,
  `status().endpoint == "http://127.0.0.1:3000/"`, and the `api` use site is
  recorded as 8765.
- `test_the_rendered_file_after_adoption_keeps_the_published_ports` —
  rendering with the resolver still produces `"0.0.0.0:3000:3000"` and
  `"0.0.0.0:8765:8765"` (the byte-for-byte file on disk today).

**`genesis_worker/tests/test_port_adoption.py`** (new) — the migration-level scenario from the report, verbatim:

- `test_a_service_running_from_before_the_marker_migration_is_adopted` — a
  marker default, a running container with a published port, no registry
  record → `status()` is `RUNNING` with that port, and a second allocator over
  the same state dir sees the persisted record.
- `test_a_marker_service_with_no_container_is_stopped_not_starting` — the
  guard against the misleading `STARTING` state.
- `test_the_registry_adopts_at_construction` — a registry over a tmp state dir
  with a stubbed binding snapshot adopts metube's published port.
- `test_the_registry_adopts_after_a_rebuild` — the rebuild path adopts for the
  rebuilt service.
- `test_the_adoption_pass_can_be_switched_off` — `adopt_published_ports=False`
  → no record is written.

**`genesis_worker/tests/test_declarative_specs.py`** — the two specs:

- `test_metube_asks_the_framework_for_its_host_port` — **unchanged** (metube
  keeps the marker).
- `test_genesis_productivity_asks_for_two_ports` — **rewritten** for the pin:
  `declared_host_port() == 3000`, `declared_port_markers() == ()`,
  `effective_host_port() == 3000`, and the template publishes the fixed ports.
- `test_the_adopters_adopt_their_published_ports` — load the real
  `metube.yaml` with a stubbed published-ports probe → the registry holds
  `metube.listen == 8098`.

### 7.3 Existing tests that must keep passing (unchanged)

`test_port_allocator.py` (the sticky and free-check rules), `test_compose_service_ports.py`
(`test_the_runtime_port_follows_the_allocation` asserts `effective_host_port()
is None` before the first start — still true for a service that never ran and
never published a port), `test_docker_service_base.py`
(`test_effective_host_port_reports_the_record_before_the_first_start`),
`test_declarative_specs.py`, `test_yaml_loader.py`, `test_services_registry.py`,
`test_plugin_boundary.py` (the touched `utils/` modules stay a leaf — no new
imports outside `utils` + `contracts`), `test_api.py` (no API change).

### 7.4 On-node verification after the deploy (human step, part of the deploy)

```
curl -s http://127.0.0.1:20985/v1/services | jq '.[] | {name, state, web_ui_endpoint}'
#   genesis-productivity -> running, http://p3:3000/
#   metube               -> running, http://p3:8098/
cat ~/.local/state/genesis-worker/port_allocations.yaml
#   allocations: {metube: {listen: 8098}}
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E 'metube|genesis-productivity'
#   metube                0.0.0.0:8098->8081/tcp
#   genesis-productivity  0.0.0.0:3000->3000/tcp, 0.0.0.0:8765->8765/tcp
```

Then (optional, Gen's call): restart metube and confirm it comes back on 8098.

---

## 8. Risks

1. **The adoption pass runs in both worker processes at startup.** The
   correctness argument is ADR-043 §4's: the mutation happens inside the
   exclusive `flock` **and** the registry is re-read inside the lock. Process A
   writes `metube.listen = 8098`; process B, taking the lock afterwards, reads
   the record and skips (adoption never overwrites). If the read were outside
   the lock, both processes would write the same value — harmless here, but it
   would be a lost-update bug the moment the rule changes. *Mitigation: the
   read-inside-the-lock is part of the `adopt()` contract, and
   `test_adopting_twice_is_idempotent` + `test_adopt_is_visible_to_a_second_allocator`
   pin it.*

2. **The compose template parse is the fragile half.** It is best-effort by
   construction: a template that does not parse, a `ports:` entry without a
   container port, a container port the template does not mention, or a stopped
   container all yield **no observation** — never a wrong record. The cost of a
   miss is the old symptom (the service stays `starting`, now with an
   explanatory message). The live case that would depend on it —
   genesis-productivity — is removed by the pin, so the compose parse is
   exercised by tests only until another compose spec adopts a marker.
   *Mitigation: the parse's failure modes are individually tested.*

3. **The pinned `genesis-productivity` spec and the reserved set.** 3000 joins
   `_reserved_ports()` via `declared_host_port()`; 8765 does not (the reserved
   set covers only the primary port). Neither is inside the dynamic range
   `18000-18999`, so the allocator can never hand either out; 8765 is
   additionally covered by ADR-043 §5 rule 3 (declared container bindings are
   reserved) while the container exists. The residual gap — a service's
   *secondary* fixed port is not reserved, and its container is removed on
   stop — is pre-existing (genesis-agent-context's 8766 has the same shape) and
   is **not** fixed here: fixing it means widening the `declared_host_port()`
   contract to a plural form, a separate decision.

4. **The adoption pass writes to the node's live state directory at worker
   startup — and the test suite constructs registries against the live state
   directory.** `test_services_registry.py` builds `ServiceRegistry(Settings())`
   (~10 sites) with the *real* `~/.local/state/genesis-worker`; without a
   guard, running the suite on the dev box would write
   `port_allocations.yaml` into the developer's live state and run a
   `docker ps -aq` + N × `docker inspect` per registry construction. The fix is
   the explicit `adopt_published_ports: bool = True` keyword on
   `ServiceRegistry.__init__`: production keeps the default, and the live-state
   test constructions pass `adopt_published_ports=False` (a mechanical change
   in one test file). The suite's docker-binding probe is stubbed in
   `conftest.py`, so no test shells out to the docker daemon.

5. **The adoption pass reads the docker daemon at startup.** One `docker ps -aq`
   plus one `docker inspect` per container (~20 on this node) per worker
   process, once per start. The pass is skipped entirely when no registered
   service declares an auto-port use site (after the pin: metube is the only
   one, so the pass runs). *Accepted; the allocator already does the same
   probing on every `allocate()`.*

6. **A genuine node-level conflict is not recorded.** If two of our own
   containers publish the same host port, `adopt()` refuses it for the second
   service (another service's record or a reserved port) and that service stays
   unallocated — `starting` with the explanatory message. That is the intended
   behaviour: the framework refuses to bless a conflict. It is *not* silently
   resolved, and it is not resolved by this task.

7. **The status message changes an API payload field.** `ServiceStatus.message`
   already exists and already reaches `GET /v1/services`; the change only
   fills a field that was empty on that branch. A consumer that keys on an
   empty message would see text. Low risk; noted for completeness.

8. **The pin changes what the configure panel shows for genesis-productivity.**
   Its two ports are now ordinary integers: the panel renders the number input
   instead of the "assign automatically" checkbox, and an Apply would write a
   pinned override. That is the intended, pre-ADR-043 behaviour. If Gen later
   wants the ports dynamic again, the marker is one line — and the adoption
   pass (ADR-045) is what makes that migration safe.

9. **The adoption is sticky, permanently.** Once `metube.listen = 8098` is
   recorded, metube keeps 8098 across restarts unless the record is released
   (`release()` is only called when the user pins a fixed port instead). This is
   ADR-043's sticky rule, unchanged — but it means the *current* port becomes
   the permanent one. That is the intent ("the running container is the
   truth"), and it is the same trade-off the report already accepted.

10. **The `published_host_ports()` method lands on the declarative base.** It
    is framework code under `utils/services/`, not a plugin, so ADR-009's
    boundary is unaffected; `test_plugin_boundary.py` must stay green (the
    touched modules import only from `utils` and `contracts`).

11. **The uv kind's status message** is included for symmetry; no uv spec
    exists today, so it is untested in production — but it is the same branch
    and the same one-line change, and the unit test covers the helper.

---

## 9. What this plan does not do

- It does not move any port, restart any container, or touch the live state
  directory by hand. The registry file is written by the worker itself, after
  the deploy.
- It does not migrate the other thirteen specs to markers.
- It does not change the dynamic range, the bind probe, the marker grammar, the
  loader's rounds, or the use-site rule.
- It does not widen `declared_host_port()` to a plural form (§8 risk 3).
- It does not add an HTTP API field (ADR-043 §13).
- It does not touch the genesis-productivity repo itself — only the worker's
  spec for it.
