# ADR-043: Service-served artefacts — `GET /v1/services/{name}/artefacts/{artefact}`

## Title

Service-served artefacts — one generic read route on the existing per-service
route scheme, so a service plugin can serve a document it generates (llama-swap's
pi `models.json`) to the orchestrator.

## Status

Accepted. Numbered 043 rather than 042: ADR numbers 042 are claimed by two
other in-flight tasks in this repo (llama-cpp forks, port allocation), and the
numbering is never reused. The cross-repo plan for this work was written as
"ADR-042"; this ADR is that document.

## Context

The orchestrator drives the fleet. Today it pushes configuration to a worker
(the `materialize_orchestrator_config` hook, ADR-038) but cannot read a document
a worker generated. The next step in the cluster-wide pi model configuration
work needs exactly that: each node owns the truth about the models its
llama-swap serves (context window, input modalities, max output, reasoning
flag), and that truth is derived from the node's own `evaluate_all` output —
the same data that drives the node's `config.yaml`. The orchestrator cannot
synthesise it.

The worker's pi export already exists
(`genesis_worker/services/llama_swap/export_pi_config.py`), derived from the
same `evaluate_all` output that drives `config.yaml`, so the export and the
llama-swap config cannot drift. What is missing is transport: the orchestrator
must be able to fetch that document from a node programmatically.

Forces:

- **No hard-coded path in FastAPI.** The framework must not know that
  llama-swap produces a `models.json`. Each service plugin serves the
  artefacts it generates.
- **The plugin boundary holds (ADR-009).** The framework talks to the ABC in
  `contracts/service.py` and nothing else. A new capability becomes an
  optional method on `InferenceService`, defaulted so no existing plugin
  breaks.
- **The existing route scheme is the right home.** The per-service routes
  (`/v1/services/{name}`, `/status`, and the four ADR-038 POSTs) already
  address a service. An artefact is a thing a service serves, so it belongs
  under the same prefix.
- **Addressing must match the gateway.** The orchestrator's gateway
  (bifrost) addresses a model as `<provider>/<model>`, where `<provider>` is
  the fleet host the node was registered under. The node's own export uses
  bare model ids and a slugged hostname-derived provider key. The orchestrator
  therefore has to be able to ask for the *gateway-addressed* shape of the
  same document — the provider key it chose, verbatim, with the model ids and
  names prefixed by it.
- **The two shapes must not be confused.** Picking the wrong one produces ids
  that do not route. Both need tests.
- **No new capability flag.** ADR-038 established that capability gating is
  done by the presence of a member (e.g. `installs()`), not by a new boolean
  on `ServiceCapabilities`. "This service serves no artefact `X`" is answered
  by a 404.

## Decision

### 1. One generic artefact route on the existing per-service scheme

```
GET /v1/services/{name}/artefacts/{artefact}?{query params}
```

- The framework resolves `{name}` to a registered service. Unknown name →
  `404 {"detail": "unknown service: {name}"}`.
- The service plugin decides whether it serves `{artefact}`. Not served →
  `404 {"detail": "service {name} serves no artefact {artefact!r}"}`.
- The plugin raised an exception while building the artefact → `500` with the
  exception's message in `detail`.
- Query parameters are passed through to the plugin verbatim as a
  `Mapping[str, str]`. The framework never interprets them.
- The response body is the artefact document (JSON). There is no response
  model: the artefact shape is plugin-defined.

### 2. `InferenceService.artefact()` — optional, defaulted

```python
def artefact(
    self,
    name: str,
    *,
    catalog: Catalog,
    params: Mapping[str, str] | None = None,
) -> dict | None:
    """Serve a generated artefact by name; ``None`` when this service has none."""
    return None
```

`None` means "this service serves no artefact under that name" and becomes a
404. The default implementation returns `None`, so every existing plugin —
Python and YAML-declared — keeps working unchanged.

### 3. The pi export gains a provider-addressed shape

`export_pi_config.build_provider_from_configs` (and
`build_provider_for_catalog`) take a `provider` parameter, replacing the
former `hostname` parameter:

- `provider=None` — today's behaviour, unchanged: the provider key is this
  machine's hostname, slugged, and the model ids and names stay bare. This is
  the standalone shape the worker's own Pi export page and
  `write_agent_config` produce.
- `provider="p3"` — the gateway-addressed shape: the provider key is that
  name **verbatim** (no slugging — the fleet host is what the gateway holds),
  and every model's `id` and `name` is prefixed with `p3/`. Every other field
  (`contextWindow`, `input`, `maxTokens`, `reasoning`, `cost`) is copied
  untouched.

`InferenceService.export_for_agent` gains the same keyword-only
`provider=None` parameter, so every existing call site stays
source-compatible.

### 4. llama-swap serves `models.json`

`LlamaSwapService.artefact("models.json", ...)` returns the pi export. The
`provider` query parameter selects the shape: present and non-empty →
gateway-addressed, absent or empty → standalone. Any other artefact name
returns `None` (404).

The export runs the same evaluation pipeline that produces `config.yaml`, so
the served document cannot drift from the node's llama-swap configuration.

## Plan

`docs/arch/plans/plan-043-service-served-artefacts.md` — the worker half of
the cross-repo plan for cluster-wide pi model configuration. The orchestrator
half lives in `genesis-infrastructure-toolkit`
(`docs/arch/plans/plan-015-cluster-pi-model-config.md`).

## Consequences

**Positive**

- The orchestrator can read a node's generated artefacts over HTTP; the
  direction of travel is now bidirectional (push config, read artefacts).
- The route is generic. A future service can serve a workflow list, a model
  catalogue, or a health report without a framework change.
- The plugin boundary is intact: framework code calls one ABC method, the
  plugin decides what it serves.
- The gateway-addressed export reuses the exporter's single evaluation
  pipeline, so the two shapes cannot drift from each other or from
  `config.yaml`.

**Negative**

- The worker contract grows one optional method (`artefact`) and one
  optional keyword (`provider` on `export_for_agent`). Both are defaulted, so
  no existing plugin breaks.
- The orchestrator side is inert until this backend is deployed to every
  node; until then the fetch 404s and the orchestrator says so per node.
- Two shapes of the same document exist (standalone and gateway-addressed).
  Both are tested; the artefact route's `provider` parameter is what selects
  between them.

**Neutral**

- The provider name is used verbatim, so a fleet host with a dot in it
  (`yoga.local`) becomes a provider key and an id prefix with a dot in it.
  That is deliberate: it is the key the gateway holds.
- The slugging of the worker's own hostname stays for the standalone export.
- No new capability flag: `ServiceCapabilities` is unchanged.

## Cross-repo contract

The orchestrator-side mirror of this contract is documented in
`docs/notes/orchestrator-service-control.md`, which now describes the artefact
route alongside the four ADR-038 write endpoints.
