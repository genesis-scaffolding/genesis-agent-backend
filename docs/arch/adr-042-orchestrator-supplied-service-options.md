# ADR-042: Orchestrator-supplied service options

## Title

Orchestrator-supplied service options

## Context

ADR-038 gave the orchestrator a way to push a config body to a worker
service: `POST /v1/services/{name}/start` with `{"config": {...}}`, and
the worker materialises it through the `materialize_orchestrator_config`
pre-start hook. The body is opaque to the worker — the framework hands
it to the hook verbatim.

The cluster ingress proxy (ADR-015 in the orchestrator repo) needs more
than a config file. Its TLS material — the DNS provider, the DNS
credential and the ACME contact address — has to reach the container as
*options*, because the container's command line and environment are
built from the service's option values. The orchestrator owns those
settings (the admin types them into the Ingress page), and the decision
recorded on 1 Nov 2026 is that the orchestrator is the primary input:
on deploy / refresh it pushes the route table **and** the option values,
and the worker applies them.

Three ways to carry the option values existed:

1. A second endpoint or a new body shape just for options.
2. The orchestrator writing the container's config file itself (the
   Traefik static configuration).
3. A second key in the existing config body, applied by the worker
   through the option machinery it already has (ADR-036: the option
   schema, the per-service override sidecar, the rebuild path).

Option 3 was chosen. The worker's option machinery is the single place
where "what options does this service have" lives — a second endpoint
would have created a parallel store, and writing the container config
from the orchestrator would have meant the worker no longer owns how
its services are rendered.

Two properties of the existing machinery made this cheap:

- the option values are validated against the service's option schema
  (the same schema the Configure panel uses), and
- the per-service override sidecar is read live on every rebuild, so a
  value written to it is picked up by the next `rebuild` without a
  settings refresh.

## Decision

We will treat an `options` key in the orchestrator's config body as the
service's option values.

- The worker's start path (`GenesisWorker.start_service`) reads the
  `options` section of the config body before the start.
- The values are merged into the service's per-service override
  sidecar (`<config_dir>/services/<name>.overrides.json`, mode 0600) —
  the same store the Configure panel writes to — and the service is
  rebuilt so the new values are the ones the start renders.
- A config body without an `options` section changes nothing: the
  service keeps whatever its options currently say.
- The `options` section is not written to any file by the
  materialisation hooks; only file-shaped keys are.

The service must be a declarative (YAML) service: the option schema
and the rebuild path only exist there. Applying options to a Python
service raises rather than silently doing nothing.

## Status

Accepted

## Consequences

- Positive: the orchestrator can drive a service's whole configuration
  (config file + options) with one request. The ingress proxy's TLS
  settings live in one place — the orchestrator's Ingress page — and
  the worker holds no parallel copy of them.
- Positive: the values are validated against the service's own option
  schema, so a bad value fails the start instead of reaching the
  container.
- Positive: the values are persisted (sidecar, 0600), so they survive a
  worker restart — the proxy keeps renewing its certificate.
- Negative: applying the options replaces the sidecar's map-typed
  entries with the pushed values for the same keys. The Configure panel
  and the orchestrator write to the same store; the last writer wins
  for the keys they share.
- Negative: the DNS credential now lives in two places — the
  orchestrator's secret store and the worker's override sidecar. The
  worker's copy is what the container needs; the orchestrator's is the
  admin-facing source.
- Neutral: the mechanism is generic — any declarative service can
  declare options the orchestrator pushes. Only the ingress proxy uses
  it today.
