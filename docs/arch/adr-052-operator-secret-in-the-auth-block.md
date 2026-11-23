# ADR-052: Operator secrets in the declarative `auth:` block

## Title

Operator secrets in the declarative `auth:` block — one block for a generated,
persisted, injected, copyable secret, whatever the service calls it

## Status

Accepted.

## Context

ADR-035 gave the declarative YAML services an `auth:` block for exactly one
job: the crawl4ai API token. The block declared where the token lives
(`token_file`), how it is generated (`token_generator`), which env var
carries it into the container (`token_env_var`), and which user-typed option
beats the file (`fallback_option`). A pre-start hook created the file, the
service injected the value at start time, and the `auth_token` panel showed it
with a copy button.

Bifrost v2 needs the same machinery for a different kind of secret. Upstream
(<https://docs.getbifrost.ai/quickstart/gateway/setting-up-auth>) refuses to
create the first admin account unless the operator configured a setup token
*before* the container started:

- `BIFROST_SETUP_TOKEN` in the container environment, or
- `setup_token` at the top level of `config.json` (a literal, an `env.VAR`
  reference, or a `vault.path` reference).

The dashboard then asks for the same value in *Workspace → Config → Security →
Setup token* while the first admin account is created. The token is never
persisted by Bifrost and never logged; it stops mattering once the account
exists.

So the shape of the requirement is identical to crawl4ai's — generate once,
persist, inject, show for copying — but the secret is **not** an HTTP
credential. Three things in the current framework get in the way of reusing
the block as-is:

1. The `auth_token` panel renders the caption "Authorization header:
   `Bearer <token>`" and a hardcoded `API_TOKEN` env var name. A setup
   token is pasted into a dashboard form; it is not a bearer header.
   Vaultwarden hit the same wall
   and solved it by omitting the panel entirely — its `ADMIN_TOKEN` is a
   password, and a bearer-shaped panel would mislabel it.
2. The block's vocabulary is API-credential vocabulary (`enabled_option` means
   "JWT mode", i.e. "the credential is issued by someone else"). A setup token
   has no such mode; the field is simply absent, and the block has to be
   readable as "externally managed" rather than "JWT".
3. Nothing in the framework can carry the wording. The panel API has a
   `panel_config` dict, but `render_default_status` always passes an empty
   one, so a service cannot say "this is a Setup token, paste it here".

Alternatives considered:

1. **A new panel kind** (`setup_token`) beside `auth_token`. It duplicates the
   copy-to-clipboard block and needs its own wording source; two near-identical
   renderers for one concept.
2. **General `ui.panel_config` plumbing** — a per-panel config map in the YAML
   `ui:` block, threaded from the spec through the loader into
   `render_default_status`. A general mechanism with exactly one user, and it
   puts the wording of a *secret* in the block that describes *panels*.
3. **A Python service subclass for bifrost.** The escape hatch ADR-035 names
   for everything YAML cannot express. The YAML template expresses this
   perfectly well; a Python package would also become a module the plugin
   boundary test walks.
4. **Leave the panel as it is** and let bifrost show "Bearer <token>". Cheap,
   and wrong — the operator would be told to send a bearer header to a
   dashboard form.
5. **Write the token into bifrost's `config.json`** instead of the env var.
   `config.json` on that path is owned by the orchestrator's
   `materialize_orchestrator_config` hook (ADR-038); a second writer for the
   same file is a lost update waiting to happen. The env var is the
   documented, restart-friendly path.

## Decision

We will widen the `auth:` block from "API credential" to **operator secret**,
and give the block the two pieces of wording the panel needs.

- The block keeps its existing fields with their existing meaning:
  `token_file` (where the secret is persisted, 0600), `token_generator` (how
  it is created), `token_env_var` (the env var that carries it into the
  container), `fallback_option` (a user-typed value that beats the file),
  `enabled_option` (the secret is managed externally — crawl4ai's JWT mode —
  so the worker generates nothing). The block stays optional: a service with
  no secret declares no block.
- The block gains two **optional display fields**, both defaulting to empty:
  - `label` — the heading the status page shows above the copy control
    (today's heading is "API token").
  - `usage` — the line that tells the operator what to do with the secret
    (today's line is the "Authorization header: `Bearer <token>`" caption).
  Empty means "keep today's wording", so every existing spec renders exactly
  as it does now.
- The `auth_token` panel reads the heading and the usage line from the
  service's auth block (`DockerService.auth_config()`), and falls back to the
  current wording when the block does not carry them. The
  copy-to-clipboard control is unchanged. The JWT badge keeps its current
  env var spelling (`<token_env_var>_JWT_ENABLED`): re-sourcing it from the
  block would replace one wrong name (crawl4ai's real var is
  `CRAWL4AI_JWT_ENABLED`, not `CRAWL4AI_API_TOKEN_JWT_ENABLED`) with
  another.
- bifrost.yaml declares the block: `token_env_var: BIFROST_SETUP_TOKEN`,
  `token_file: "$state_dir/setup_token"`, an `ensure_persistent_token` hook
  with the same target, `fallback_option: "$options.setup_token"`, plus
  `label` and `usage` naming the dashboard field the token goes into. The
  spec opts into the `auth_token` panel.

## Consequences

**Positive**

- Bifrost gets the crawl4ai treatment the user asked for: a token generated
  once at first start, persisted at 0600 under the state dir, injected as
  `BIFROST_SETUP_TOKEN`, and shown on the status page with a copy button.
- The block becomes reusable for every service whose secret is not a bearer
  credential, and the panel stops lying about what the secret is.
- The change is additive and default-preserving: a spec that declares no
  `label` / `usage` renders byte-identical UI to today. crawl4ai and
  vaultwarden need no edit.
- The wording lives with the secret. A YAML author declares the secret and how
  to present it in one block, next to the env var and the file.

**Negative**

- The block's vocabulary is now slightly wider than its name. `enabled_option`
  reads as "JWT mode" to a crawl4ai reader and as "managed externally" to a
  bifrost reader. The ADR is the place that resolves that; the field keeps
  its name to avoid a spec-wide rename.
- The panel's default wording is still bearer-shaped. A service that declares
  a non-bearer secret and forgets `label` / `usage` gets the wrong copy — the
  failure is silent. Vaultwarden's omission of the panel remains the honest
  choice for a password-shaped secret.
- `label` / `usage` are free-form strings with no schema. A typo is a typo in
  the UI, not a load error.

**Neutral**

- No contract method is added to `InferenceService`; the panel reaches the
  block through `DockerService.auth_config()` with a `getattr` fallback, the
  same duck-typing style the panel already uses for `auth_token` /
  `auth_enabled`.
- The `auth_token` panel keeps its name. The *heading* the operator sees is
  whatever the spec says it is.
- The token remains visible to anyone who can reach the worker's Streamlit UI
  over Tailscale — the same exposure crawl4ai's API token already has. The
  file on disk stays 0600.

## Plan

`docs/arch/plans/plan-052-bifrost-setup-token.md` — file-by-file execution:
the two display fields on the auth block, the panel reading them, bifrost.yaml
declaring the setup token, and the tests.
