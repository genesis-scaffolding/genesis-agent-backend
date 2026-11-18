# ADR-051: Declared service ports, role labels and canonical names

## Title

A service declares its routable ports — each with a role label, exactly one
primary — and the cluster-wide canonical name it answers to.

## Status

Proposed.

## Context

A declarative service spec describes **one** port. The docker kind has
`container.listen_port` (the host port) and `container.internal_port` (the
port inside the container), plus a `container.extra_ports` list that no
shipped spec uses. The uv kind has one `listen_port`. The compose kind has no
port field at all: its port comes from the `listen_port` **option** that the
compose template publishes.

The framework exposes one endpoint per service (`runtime_endpoint` /
`web_ui_endpoint`), so the orchestrator can build exactly one route per
service — one hostname, one upstream.

Two shipped services do not fit that shape. `genesis-productivity` publishes
two ports (the Streamlit UI on 3000 and the FastAPI API on 8765) and
`genesis-agent-context` does the same (3001 / 8766). The second port is
invisible to the framework: the worker does not report it, the orchestrator
cannot route it, and the operator has to hard-code `p3:8765` for the CLI —
there is no domain name for it.

The orchestrator also wants a **canonical name** per cluster-wide service: a
node-independent address (`bifrost.<domain>`, not `p3:8090` and not
`bifrost-p3.<domain>`), granted only to a cluster-wide **single-instance**
service. Today the operator types that name by hand into the ingress page's
per-route hostname override. The worker is the only place that knows what a
service *is*, so the declaration belongs here — but the worker must not learn
the base domain (the orchestrator owns the routing knowledge).

Forces:

- **One source of truth per fact.** A port's host number, its role and its
  canonical name must not be declared twice.
- **The shipped specs keep working.** Twenty specs exist today; none of them
  may need editing because of this decision, and none of them may change the
  hostname the orchestrator derives for them.
- **A role label is routing material.** It ends up in a hostname
  (`api-genesis.<domain>`), so it must be a DNS label.
- **A port may not be known until start.** A host port can be an
  `$auto_port.<name>` marker, resolved by the allocator at start time.
- **The spec models are `extra="forbid"`.** A new key has to be declared on
  every kind's model, and the loader must resolve it.
- **The read API stays additive.** The orchestrator reads `GET /v1/services`;
  a new field there is a contract change that has to stay backward compatible
  with an older worker.

The alternatives:

- **(a) Keep one port per service and let the operator configure the API port
  by hand.** The `p3:8765` problem stays exactly as it is, and the canonical
  name cannot cover a second port.
- **(b) Extend `container.extra_ports` with a `label` and add a label to the
  primary.** The list already exists, but it is docker-only, has no notion of
  a primary port, and the compose kind — the only kind that ships a
  multi-port service — cannot use it (compose publishes through its
  template). The concept would live in the wrong place.
- **(c) A top-level `ports:` list that every kind uses, with the primary
  marked.** One concept, one place, one source of truth. The legacy fields
  become the shorthand for the single-port case.

## Decision

We will give the declarative spec a **top-level `ports:` list** — the
service's routable ports, each with a role label, exactly one of them
primary — and a top-level **`canonical_name:`** — the cluster-wide,
node-independent label the service answers to.

### 1. The shape of `ports:`

```yaml
ports:
  - label: ui            # the role label (a DNS label)
    primary: true        # exactly one primary per service
    host: $options.listen_port   # the host port (an int or a $auto_port marker)
    container: 3000      # the port inside the container (defaults to `host`)
```

- `label` — the **role** of the port (`ui`, `api`, …). A free string,
  validated as a DNS label; the vocabulary is not closed, because a new role
  must not need a framework change.
- `primary` — exactly one entry per service is the primary. The primary is
  the port that earns the service's bare name.
- `host` — the host port the client connects to. An int, or an
  `$auto_port.<name>` marker resolved by the allocator at start time.
- `container` — the port inside the container (docker / compose). Defaults to
  `host` (the uv kind publishes no separate container port).

`ports:` is **the single source of truth when it is present**. The legacy port
fields must then be absent:

| Kind | Absent when `ports:` is present |
|---|---|
| docker | `container.listen_port`, `container.internal_port`, `container.extra_ports` |
| compose | the top-level `listen_port` |
| uv | the top-level `listen_port` |

The loader derives the runtime fields from the block: the primary entry
becomes `listen_port` / `internal_port`, the other entries become
`extra_ports` (docker) — so the docker runner, the compose template and the
uv runner are unchanged.

### 2. The legacy form stays

A spec **without** `ports:` keeps today's shape: one implicit port (the
existing listen port) and **no role label**. The loader synthesises that
single port, so the framework sees exactly one port either way. The twenty
shipped specs need no edit, and their derived hostnames do not change.

### 3. The rules the loader enforces

1. **Exactly one primary.** A `ports:` block with zero or two primaries is a
   load-time error.
2. **Distinct labels.** Two ports of one service may not share a label.
3. **Every port of a multi-port service is labelled.** A service with more
   than one port must give each port a label — the labels are what keep the
   per-port hostnames apart.
4. **Labels are DNS labels** — lowercase `[a-z0-9-]`, no leading or trailing
   dash, at most 63 characters.
5. **Distinct host ports** within one service.
6. **The uv kind declares at most one port** — its runner publishes one.
7. **`canonical_name` is a DNS label**, when present.
8. **For the compose kind, the template must publish the declared ports.**
   The compose template is the mechanism that publishes; the `ports:` block
   declares what it publishes. The loader compares the resolved template's
   published host ports with the declared ones and fails the load on a
   mismatch (a best-effort parse: a template that cannot be parsed is not
   checked).

### 4. `canonical_name:` — the cluster-wide label

```yaml
canonical_name: genesis
```

The label is the service's node-independent name. Declaring it **asserts that
the service is cluster-wide and single-instance** — the orchestrator grants
that name only while exactly one live instance of the service exists, and
withholds it (every instance keeping its per-node name) when a second
instance appears. The worker cannot enforce that (it sees one node); it
declares the intent, and the orchestrator checks it against the fleet.

The label may differ from the service name (`genesis-productivity` →
`genesis`). The worker never composes a hostname: the base domain belongs to
the orchestrator.

### 5. The ports reach the orchestrator

`GET /v1/services` gains two additive fields:

```json
{
  "name": "genesis-productivity",
  "canonical_name": "genesis",
  "ports": [
    {"label": "ui",  "primary": true,  "endpoint": "http://p3:3000/"},
    {"label": "api", "primary": false, "endpoint": "http://p3:8765/"}
  ]
}
```

- `ports` — the service's routable ports, in declaration order. `label` is
  `null` for the implicit legacy port. `endpoint` is the service's own URL
  for that port (`null` when the service is not running or the port is not
  allocated yet).
- `canonical_name` — the declared label, or `null`.

An older worker sends neither field; the orchestrator keeps today's
single-route behaviour. The list row stays a small summary — the detail row
(`GET /v1/services/{name}`) is unchanged.

### 6. The allocator reserves every declared port

The port allocator keeps every registered service's **fixed** host port off
the table. Today it only sees the primary (`declared_host_port()`). A service
that declares a second fixed port would have that port handed out to another
service. The framework therefore asks each service for **all** its fixed host
ports and reserves them.

## Consequences

**Positive**

- A multi-port service is describable: `genesis-productivity` declares its UI
  and its API, the worker reports both, and the orchestrator can route both.
  The operator stops hard-coding `p3:8765`.
- The canonical name becomes part of the service's own definition, next to the
  thing it names — it cannot go stale, and a new cluster-wide service needs
  one line, not a cross-repo edit.
- The role label is declared once and used everywhere: the hostname, the
  route table, the reachability map.
- The allocator stops being able to hand out a port a service already owns.
- The twenty shipped specs are untouched and keep their hostnames.

**Negative**

- The spec schema grows a concept: a new top-level key on three kinds, a new
  list type, and a set of cross-field rules. A spec that declares `ports:`
  and forgets to remove `container.listen_port` fails to load — deliberately.
- The loader must derive the runtime port fields from the block, which makes
  "where does the port come from" a question with two answers depending on
  the spec's shape.
- The compose kind's template and its `ports:` block must agree; the loader
  checks it, but the check is a parse of a free-form template.
- The read API grows a nested list on every service row.
- A service that is declared cluster-wide but runs twice is a misconfiguration
  the worker cannot catch; the orchestrator withholds the name and says why.

**Neutral**

- `container.extra_ports` has no users today and is superseded by `ports:`.
  It stays in the schema for the legacy shape and gains no new members.
- The legacy single-port service has no role label, so its derived hostname
  is unchanged (`ntfy-p3.<domain>`). A multi-port service's ports are
  labelled, and their hostnames carry the label.
- The canonical name is a declaration, not state: nothing is persisted, and
  the orchestrator composes the hostname itself.

## Plan

`docs/arch/plans/plan-048-declared-service-ports-and-canonical-names.md` —
the spec models and their validators, the loader's derivation of the runtime
port fields, the port list on the service contract, the `GET /v1/services`
fields, and the allocator's reservation of every declared fixed port.
