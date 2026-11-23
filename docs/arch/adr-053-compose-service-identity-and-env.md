# ADR-053: Compose service identity and user-supplied environment

## Title

A compose service declares the uid/gid it runs as, and the user's `extra_env`
map reaches the container through a framework-managed env file.

## Status

Proposed.

## Context

Two defects in the ROMM declarative service (`services/_declarative/romm.yaml`,
`kind: compose`) forced the question this ADR answers.

**The library is locked.** ROMM's media-vault ROM directory is bind-mounted
into the container, but everything ROMM writes there is owned by **root**: the
desktop user cannot drop a ROM into a platform directory, rename a file or
delete one. Two causes:

- The upstream image has no `USER` directive, so the container runs as uid 0.
- The compose kind never pre-creates the host directories its template binds.
  `ComposeService._ensure_volume_dirs()` walks `config.extra_volumes`, which
  is empty for a compose spec (a compose spec declares its mounts inside the
  template), so Docker creates the missing bind-mount sources as root.

**The metadata providers are unreachable.** ROMM reads its metadata-provider
credentials from the environment (`SCREENSCRAPER_USER` /
`SCREENSCRAPER_PASSWORD`, and a long tail of other providers). The compose
template hard-codes its `environment:` block, so the user cannot set any of
them.

The forces:

- **The compose kind has no notion of the container's identity.** Every
  shipped compose service runs as its image's default user. There is no
  framework field for it and no convention.
- **`extra_env` means two different things.** In the docker kind it is the
  container's environment (`docker run -e`). In the compose kind it only
  reaches the `docker compose` **process** environment — the template's
  interpolation source — so the container never sees it. The tutorial even
  states that `env_map` / `mount_map` are docker-kind features.
- **Compose cannot inject an arbitrary variable into one service of a
  multi-service stack** without either naming it in the template (the
  `environment: [NAME, ...]` list form) or rewriting the compose YAML
  (unsafe: PyYAML's YAML 1.1 booleans would mangle `no` / `on` / `off`).
- **The long tail must not need a spec edit per variable.** The user asked
  for every provider to be configurable "in case I use them in the future";
  a typed option per upstream variable does not scale (there are ~19 today
  and upstream adds more).
- **The framework must not parse and rewrite the template's YAML.** The
  compose file is the operator's artefact.

The alternatives:

- **(a) A typed option per provider variable** (`screenscraper_user`,
  `igdb_client_id`, …). A huge configure form, and every upstream addition
  is another spec edit.
- **(b) The compose list form** (`environment: [SCREENSCRAPER_USER, …]`,
  forwarded from the `docker compose` process environment). Zero framework
  change — the framework already puts the user's map in the process
  environment — but the set of forwardable names is frozen in the template:
  a provider ROMM adds later needs a YAML edit. It stays a documented
  fallback.
- **(c) A framework-managed env file** the template references with
  compose's own `env_file:`. Any variable the user puts in the map reaches
  the container, with no per-variable framework or spec knowledge.

## Decision

We will give the compose kind two things: an identity the service declares
in its own template, and an environment the user supplies without the
template naming a single variable.

### 1. The service declares the uid/gid it runs as

The identity lives in the template's own compose `user:` directive — no
framework field, because compose already has one and the compose kind does
not rewrite the template:

```yaml
compose_template: |
  services:
    romm:
      image: $images.romm
      user: "$options.puid:$options.pgid"   # 1000:1000 by default
```

The framework's job is to make the **host** side match: the pre-start
`ensure_writable_dir` hook creates the bind-mount targets as the host user
before Docker sees them, so Docker never creates them as root. The hook
chowns them to the container's uid when the worker is privileged enough, and
falls back to a bounded mode when it is not.

The uid/gid are ordinary `int` options (`puid` / `pgid`, default 1000) so an
operator whose host uid differs can set them. The default is not arbitrary:
it is the uid of the image's own `romm` user, which also owns `/romm` and
`/redis-data` inside the image, so the container keeps working as non-root.

### 2. `extra_env` means the container's environment — in both kinds

The compose config's fields are split accordingly:

| Field | Meaning |
|---|---|
| `extra_env` | the container's environment (the user's map) |
| `user_env` | the user's `extra_env` option map, as configured |
| `compose_env` | the `docker compose` process environment (interpolation source) |

The compose **process** environment keeps the spec's own `env:` block and the
user's map, so a template may still forward a named variable from it (the
list form, alternative (b)).

### 3. The env file

A compose spec may declare `env_file: <host path>` — a `$placeholder`-
resolved path, like every other path field:

```yaml
env_file: "$state_dir/romm.env"

compose_template: |
  services:
    romm:
      env_file:
        - $state_dir/romm.env
```

The framework writes the user's `extra_env` map there before the stack is
brought up: mode `0600` (the map carries credentials), atomic (a sibling tmp
file plus `os.replace`), and a full rewrite, so a key the user removed does
not survive.

The values are written in compose's own env-file syntax, so they round-trip
exactly. Compose's parser (compose-go's `dotenv`) reads a **double-quoted**
value like a shell does: `\\` is a backslash, `\"` a quote, `\$` a dollar
sign, `\n` / `\r` / `\t` the control characters. A single-quoted value is the
odd one out — compose keeps its backslashes verbatim (only `\'` becomes a
quote) — so a password containing a backslash would arrive mangled, and an
unknown escape loses its backslash. Double quotes are therefore the only
encoding that round-trips every value.

A variable the user did not set is **absent** from the container, not an
empty string.

### 4. The loader enforces the pairing

- A spec with an `extra_env` option must declare an `env_file` — otherwise
  the user's env vars would be silently dropped.
- A spec that declares an `env_file` must reference that exact path in the
  template's `env_file:` — the file the framework writes is inert unless the
  template points at it.

Both are load-time errors, named with the offending path.

### 5. Precedence

Compose gives `environment:` precedence over `env_file:`, so the typed knobs
in the template (`DB_*`, `ROMM_FRONTEND`) keep priority over a map entry
with the same name. This is the opposite of the docker kind, where the
user's map wins over the YAML's `env:` block (ADR-036 §5). The difference is
compose's own rule, not a framework choice: the compose kind cannot rewrite
the template's YAML, so it cannot make the map win.

### 6. The compose kind does not parse and rewrite the template

The env file is delivered by compose itself. The framework writes one file
and checks that the template references it; it never rewrites the compose
YAML.

## Consequences

**Positive**

- A compose service whose image runs as root no longer hands the host user a
  root-owned library: the service declares its uid/gid and the framework
  makes the host side match.
- The long tail of upstream env vars is configurable without a spec edit —
  one `env_map` option covers every provider, present and future.
- `extra_env` means the same thing in both kinds: the container's
  environment.
- The user's env values round-trip exactly, whatever they contain.

**Negative**

- A new compose-kind field (`env_file`) and two loader validations; a spec
  that declares an `extra_env` option and forgets the `env_file` fails to
  load.
- The env file must exist before `pull` / `up`; the framework writes it in
  `start()`, and `docker compose up` fails on a missing one.
- The compose file and the env file are two artefacts to reason about.
- The precedence differs between the kinds (see §5) — a user who sets
  `DB_USER` in the map does not get it in a compose service.

**Neutral**

- The docker kind is untouched.
- `ensure_writable_dir` is reused as-is; the compose kind only declares the
  entries.
- The files a container created as root before the switch stay root-owned:
  the framework cannot chown what it does not own. The rollout carries a
  one-time `chown -R` step.

## Plan

`docs/arch/plans/plan-049-romm-permissions-and-provider-env.md`.
