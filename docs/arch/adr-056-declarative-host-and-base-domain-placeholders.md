# ADR-056: Declarative host and base-domain placeholders

## Title

Declarative host and base-domain placeholders

## Context

The declarative service loader currently resolves path placeholders, service
options, image references, and dynamic ports against the worker context. It has
no way to express the worker's host identity or the cluster's public base
domain.

`genesis-productivity` needs both values. Its default ntfy topic is
`genesis-<hostname>-<user>` (ADR-027 in the `genesis-productivity` repo), but
the service runs in a container. Without an explicit Compose hostname,
`socket.gethostname()` inside that container returns the generated container
ID, so the topic changes after every recreation and no longer matches the
topic registered on a phone.

The ntfy URL is the second half of the problem. The current deployment
workaround hard-codes a host and port in the container environment. The
cluster ingress already has an admin-supplied base domain, but that domain
lives in the orchestrator and is not currently carried to the worker.

The declarative placeholder contract is therefore extended across the worker
and orchestrator. This changes a framework/plugin-facing context and a
documented loader contract, so it needs an ADR rather than a routine field
addition.

## Decision

We will add two framework-level placeholders to declarative service specs.

### `$hostname`

`$hostname` resolves to the worker host's hostname from the existing
`HostInfo` snapshot. The loader normalises it with the same policy as
`genesis-productivity`'s `_safe_hostname()`:

1. take the first dot-separated label;
2. lowercase it;
3. replace every character outside `[a-z0-9-]` with `-`;
4. trim leading and trailing `-`;
5. fall back to `localhost` when the result is empty.

The hostname is not seeded or copied from the orchestrator. The worker already
observes it on the host, and the declarative loader receives that observation
through `ServiceContext.host_info`.

### `$base_domain`

`$base_domain` resolves to a new framework-level `Settings.base_domain`
field. Its environment contract is the exact key `GENESIS__BASE_DOMAIN` in
the worker's `.env` or process environment. The field is optional and is
carried to services through `ServiceContext`.

When the field is unset or blank, `$base_domain` resolves to the deliberate
placeholder `no-base-domain`. A rendered URL such as
`https://ntfy.no-base-domain` is visibly invalid instead of silently naming
an unrelated host.

### `genesis-productivity` spec

The `genesis-productivity` Compose spec will set:

- `hostname: "$hostname"` on the application service, so the container's
  `socket.gethostname()` is the stable worker hostname; and
- `GENESIS__NTFY__URL: "https://ntfy.$base_domain"` in the application
  environment.

No new settings UI is added. The orchestrator's existing per-node worker
`.env` editor remains the operator surface, and the companion
`genesis-productivity` settings page remains the runtime surface for the
ntfy URL and topic.

## Status

Accepted

## Consequences

- Positive: a containerised `genesis-productivity` instance derives the same
  default topic before and after container recreation, provided the worker
  hostname does not change.
- Positive: declarative services can consume the cluster's public base
  domain without hard-coded hosts or ports.
- Positive: an unset base domain fails visibly at the rendered URL rather
  than pointing at a plausible but wrong host.
- Negative: `ServiceContext` gains one framework-level field. Existing
  service plugins receive it but do not need to use it.
- Negative: hostname normalisation can collapse two raw hostnames with the
  same first label into the same placeholder value. This is intentional and
  matches the ntfy topic policy.
- Negative: changing the base domain affects a running declarative service
  only after the worker has reloaded the setting and the service has been
  rebuilt or restarted with the new resolved template.
- Neutral: `localhost` remains the fallback for an empty or
  unsanitizable worker hostname.
- Neutral: the orchestrator owns how `GENESIS__BASE_DOMAIN` is seeded; the
  worker only defines and consumes the contract. See ADR-022 in
  `genesis-infrastructure-toolkit`.

## Cross-references

- ADR-035 in this repo: the declarative service template and placeholder
  resolution contract.
- ADR-027 in `genesis-productivity`: ntfy notifications and the
  `genesis-<hostname>-<user>` default topic.
- ADR-022 in `genesis-infrastructure-toolkit`: deployment-time seeding of
  `GENESIS__BASE_DOMAIN` from the cluster's `ingress.domain` setting.
