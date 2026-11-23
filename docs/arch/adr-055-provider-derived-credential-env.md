# ADR-055: Provider-derived credential environment variable

## Title

Provider-derived credential environment variable for declarative services — a
spec may declare a `credential_env` block that maps the value of one of its own
options (a DNS provider name) to the name of the environment variable the
service's credential must be written under; the framework resolves the name at
render time and writes the credential under it.

## Status

Proposed (2026-11-06). Written as part of the plan for task `t-2z2wtc-traefik-env`
(the ingress proxy's DNS credential was hard-wired to GoDaddy's environment
variable regardless of the selected provider). The mechanism is a change to the
declarative spec contract, so it is recorded here before the code.

Numbering: the ADR was written as 047 and renumbered to 055 when the
implementation landed — `adr-047-vault-backed-model-stores.md` had taken 047 on
`main` in the meantime, and 052–054 are claimed by live branches. ADR numbers
are sequential and never reused.

## Context

### The defect

`genesis_worker/services/_declarative/traefik.yaml` declares the ingress proxy's
DNS-01 credential as:

```yaml
      environment:
        GODADDYV3_PERSONAL_ACCESS_TOKEN: "$options.dns_api_token"
```

while the provider itself is an option:

```yaml
  dns_provider:
    type: string
    default: "godaddyv3"
```

and the provider reaches the container as a parameter:

```yaml
        - --certificatesResolvers.letsencrypt.acme.dnschallenge.provider=$options.dns_provider
```

The two are independent. Selecting any provider other than `godaddyv3` produces
a container that asks Lego for a DNS-01 challenge against that provider while
handing it a GoDaddy Personal Access Token under a name the provider does not
read. The provider finds no credential and the issuance fails. The Ingress page
in the orchestrator offers `cloudflare`, `route53`, `ovh`, `azure`,
`digitalocean`, `gandi`, `linode` and `vultr` next to `godaddyv3`, so the
mismatch is reachable from the admin UI, not only by hand-editing a spec.

The failure is not cosmetic: GoDaddy's DNS writes are currently refused (401,
task `t-famzp6`), and the fallback being considered is to move the zone's DNS
hosting to another provider. A provider switch that leaves the credential
under the old name cannot obtain a certificate at all.

### Why the credential's name is not an ordinary option value

Lego — the ACME client embedded in the official Traefik image — reads one
environment variable per DNS provider, and the name is a property of the
provider, not of the deployment. The names are not derivable from the provider
name and they are not uniform (`DO_AUTH_TOKEN`, `LINODE_TOKEN`,
`CLOUDFLARE_DNS_API_TOKEN`, `GODADDYV3_PERSONAL_ACCESS_TOKEN`).

The current spec therefore embeds one provider's credential name in a place
where the provider is a variable. Two things have to move together — the
provider and the name of the variable its credential is written under — and
today only the first one is an option.

### What the substitution engine can and cannot do today

The loader has one substitution mechanism, `resolve_placeholders`, dispatching a
flat `$` namespace (`$state_dir`, `$options.X`, `$images.X`, `$auto_port.X`).
Two properties of it decide this ADR:

1. **The compose template is one string.** `compose_template` is substituted as
   text, so a placeholder inside it is replaced wherever it appears — including
   where it is a *key* of the YAML the template carries. Verified against the
   current loader: a template line `"$options.dns_credential_env":
   "$options.dns_api_token"` renders as `"CLOUDFLARE_DNS_API_TOKEN": "the-token"`,
   and the rendered file parses back with `CLOUDFLARE_DNS_API_TOKEN` as a
   mapping key.
2. **The namespace dispatches on a key, and an unknown key survives intact.**
   `$images.X` survives the load-time rounds and is materialised at render time,
   because the resolution source (`images=`) is only supplied at render time.
   The same shape fits a credential env var name: the *value* of the provider
   option is only known to the running service, and the name must be resolved
   when the runtime command is generated.

So the mechanism needed is a new key family in the existing namespace, resolved
by the existing function, at the existing rounds — exactly the shape ADR-043
used for the auto-port marker.

### Where the provider's credential name belongs

Three places could hold the provider → credential-name mapping:

1. **The orchestrator's Ingress page.** The admin picks the provider there, so
   the page could compute the name and push it as a second option. The mapping
   would then live in the admin UI, next to a field the admin does not
   otherwise need to understand: `CLOUDFLARE_DNS_API_TOKEN` is an
   implementation detail of Traefik + Lego, not an admin-facing setting. It
   would also make the fix depend on a second repository's release, and the
   worker would keep the ability to be mis-wired (provider and name are two
   independent options).
2. **A generic framework table.** Lego-specific knowledge in
   `genesis_worker/utils/` would put a service's credential contract in the
   framework, against the framework/plugin boundary (ADR-009).
3. **The service's own spec.** The spec already pins the image
   (`traefik:v3.7`), already documents GoDaddy's credential in its header, and
   already owns the provider option. The name of the variable that provider's
   credential must be written under is service data — the same kind of data as
   the image repo or the health probe path.

Option 3 keeps the knowledge where the rest of the proxy's configuration
lives, and it keeps the worker self-sufficient: changing the provider option is
enough to obtain a certificate.

### The alternatives that were rejected

- **An explicit `dns_credential_env` option** (the provider's name is supplied
  alongside the provider). No framework change at all — the template becomes
  `"$options.dns_credential_env": "$options.dns_api_token"` and the existing
  substitution engine renders it. Rejected as the primary mechanism because it
  splits one decision across two options that can drift apart, and because it
  pushes a software-internal name into the admin UI. It stays available as the
  escape hatch for a provider the map does not cover.
- **Emitting every provider's variable** (the credential written under all
  seven names at once). No framework change either, and it works for any
  provider in the list without coordination. Rejected: the credential is then
  published under names that do not apply, the rendered compose file stops
  being readable, and the provider that is *not* selected still receives a
  credential it never asked for.
- **A new option type** (a `select` whose value carries the env var name).
  Puts a service-specific vocabulary into the option DSL, which ADR-035
  deliberately keeps closed.

### Forces

- **The framework stays generic.** The mechanism must not know what a DNS
  provider is. It maps an option's value to a string; the spec says which
  option and which values.
- **One substitution mechanism.** No second templating language, no parallel
  resolution path (ADR-043's rule).
- **The worker must not break because of a runtime value.** The provider is a
  runtime value: it can be pushed by the orchestrator (ADR-042) or typed into
  the Configure panel. A bad provider must fail the proxy's start with an
  actionable message, not the worker's startup — the service registry loads
  every spec at construction, so a load-time failure would take the whole
  worker's UI and API down.
- **The credential must not leak into a variable the provider does not read.**
  The rendered compose file carries the credential and is written `0600`; the
  variable it lands in is the provider's own.
- **Providers that need more than one credential stay out of scope.** The
  proxy carries a single `dns_api_token`; `route53` needs an access key *and* a
  secret, `ovh` needs three, `azuredns` needs five. A map from one token to
  one variable cannot express them, and pretending otherwise would ship a
  broken path.

## Decision

We will let a declarative spec declare which of its options selects a
credential's environment variable name, and which value of that option maps to
which variable. The framework resolves the name when it renders the runtime
command and writes the credential under it.

### 1. The spec block

```yaml
credential_env:
  provider_option: dns_provider
  by_provider:
    godaddyv3: GODADDYV3_PERSONAL_ACCESS_TOKEN
    cloudflare: CLOUDFLARE_DNS_API_TOKEN
```

`provider_option` names one of the spec's own options; `by_provider` maps that
option's value to the name of the environment variable the credential is
written under. The block is optional: a spec without it has no credential env
var and may not use the placeholder.

The block lives on the compose kind only (`ComposeServiceSpec`). The docker
kind writes its environment from a mapping whose *keys* are not substituted by
the loader, so a templated variable name cannot be expressed there; the uv
kind has no environment block at all.

### 2. The placeholder

`$credential_env` is a key in the existing `$` namespace, next to
`$state_dir` and `$options.X`. It resolves to the variable name the selected
provider's credential is written under.

It behaves like `$images.X`, not like `$options.X`:

- At load time the loader has no resolution source, so the marker survives the
  load-time rounds untouched — the same unknown-key fallback that keeps
  `$images.<name>` intact.
- At render time the service resolves it: the current value of the option named
  by `provider_option`, looked up in `by_provider`. The name is substituted
  into the rendered command (the rendered `docker-compose.yml` for the compose
  kind).

`resolve_placeholders`, `_resolve_string` and `_resolve_placeholder` gain one
parameter, `credential_env: str | None = None`, alongside the existing `images`
and `ports` parameters. One mechanism, one namespace, one extra resolution
source.

### 3. A provider with no mapping fails the start, not the worker

If the selected provider is not in `by_provider`, the name cannot be resolved,
the marker survives into the rendered command, and the render refuses to write
the file:

```
traefik: the provider 'azure' (option 'dns_provider') has no credential
environment variable — add it to the spec's credential_env.by_provider map
(known: cloudflare, digitalocean, gandi, gandiv5, godaddyv3, linode, vultr)
```

The refusal happens on the start path, where the service's own failure is
contained. It does not happen at load time, because a load-time failure in
`ServiceRegistry.__init__` would take the whole worker down — the provider is a
runtime value, and a runtime value must not be able to stop the worker from
starting.

The same refusal covers the two ways the marker can survive: a provider with no
mapping, and a template that uses `$credential_env` in a spec that declares no
`credential_env` block. The loader rejects a `credential_env` block whose
`provider_option` names no option of the spec, whose `by_provider` map is
empty, or that maps a provider to an empty variable name — a typo there is a
spec error and fails at load time, where spec errors belong.

### 4. The proxy's map

The ingress proxy's map is pinned to the Lego version in the image the spec
selects:

```yaml
credential_env:
  provider_option: dns_provider
  by_provider:
    godaddyv3: GODADDYV3_PERSONAL_ACCESS_TOKEN   # Lego v5.6.0
    cloudflare: CLOUDFLARE_DNS_API_TOKEN
    digitalocean: DO_AUTH_TOKEN
    gandi: GANDI_API_KEY
    gandiv5: GANDIV5_PERSONAL_ACCESS_TOKEN
    linode: LINODE_TOKEN
    vultr: VULTR_API_KEY
```

Every name is the one Lego reads, taken from the provider's own documentation
(`https://go-acme.github.io/lego/dns/<provider>/`) and from the provider's
source in `github.com/go-acme/lego` (the `envNamespace` + `Env*` constants).
The names are identical in Lego v4.35.2 and v5.6.0 for these providers.

The providers that need more than one credential (`route53`, `ovh`,
`azuredns`) are deliberately absent: the proxy carries one credential, and a
map from one credential to one variable cannot express them. A provider that is
absent fails with the message in §3, which is the honest outcome.

`godaddyv3` keeps `GODADDYV3_PERSONAL_ACCESS_TOKEN`. That is the case the
proxy is deployed with today: the credential's mapping in the rendered compose
file for the default provider is unchanged (only the comment above it moved).

### 5. The name is not an admin-facing setting

The orchestrator's Ingress page keeps pushing `dns_provider` and `dns_api_token`
and nothing else. The mapping is service data, and the service owns it.

## Consequences

**Positive**

- The proxy's credential reaches the provider that is actually selected. A
  provider switch is one option change, and the ACME issuance works without a
  second repository's release.
- The rendered compose file names the credential's variable explicitly, so
  "which variable does Lego read for this provider" is answered by reading the
  spec, next to the provider option and the image pin.
- A provider with no mapping fails with a message that names the provider and
  the known ones, on the service's own start path.
- `godaddyv3` behaviour is unchanged: the same variable, the same value, the
  same rendered file.

**Negative**

- The declarative spec contract grows: a new block and a new placeholder key.
  A spec that uses `$credential_env` without declaring the block is rejected at
  render time, and a block that names a non-existent option is rejected at load
  time.
- `resolve_placeholders` grows a third optional resolution source. It stays one
  function with one namespace, but the signature carries three.
- The mechanism is compose-only. The docker kind cannot express a templated
  variable name, because the loader substitutes environment *values*, not
  environment *keys*.
- The map is pinned to the Lego version in the image. `traefik:v3.7` is a
  floating minor tag: the `godaddyv3` provider only exists from Lego v5.6.0
  (Traefik v3.7.17) onwards, and an older v3.7.x image on a node does not have
  it at all. Pinning the image to a patch tag, or recording the floor in the
  spec, is a separate decision and is not taken here.
- The providers that need several credentials are not served by this mechanism.
  Serving them needs a credential *map* option (an `env_map`), which the
  option DSL already has and this ADR does not touch.

**Neutral**

- The map's contents are data. A new provider is a new line in the spec, not a
  framework change.
- The placeholder resolves at render time, so the variable name follows the
  provider option's value at the moment the runtime command is generated — the
  same freshness the image markers have.
- The explicit-option escape hatch remains available: a spec may keep an
  ordinary option for the variable name and use it directly, at the cost of
  the provider and the name drifting apart.

## Plan

`docs/arch/plans/plan-055-provider-derived-credential-env.md` — the
investigation and implementation plan for task `t-2z2wtc-traefik-env` (worker
repository). The plan implements §1-§5 of this ADR, adds the proxy's map, the
loader's placeholder, the compose kind's resolution and refusal, the tests, and
the tutorial section.
