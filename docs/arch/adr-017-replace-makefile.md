# ADR-017: Replace the repo Makefile with a dev/release workflow

## Title
Replace the repo Makefile with a dev/release workflow targeting `genesis_worker`.

## Context

The repo-root `Makefile` was written for the legacy `bin/` scripts and writes
to repo-root state (`recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`,
`pi-models.json`). The migration to the `genesis_worker` package has reached
the point where every script the Makefile wrapped has been retired (commit
`f4e039d`). The Makefile itself is the last legacy artifact, and its targets
(`catalog`, `config`, `all`, `up`, `install-model`, `pi-models.json`,
`pi-install`, `pi-print`) all reference code paths that no longer exist.

The user has been dogfooding the `genesis_worker` package on this machine
for some time and considers it the production path.

## Decision

We will replace the Makefile with a small, dev/release workflow whose
targets are: `help` (default), `install`, `test`, `test-fast`, `lint`,
`typecheck`, `ui`, `env-init`, `build`, `clean`.

The new Makefile will not wrap any of the `genesis_worker/cli/*` modules.
Users reach those via `uv run python -m genesis_worker.cli.X`.

## Status
Accepted.

## Consequences

Positive:
- The Makefile no longer pretends to drive a system that doesn't exist.
- One combined `test` target matches the gate in AGENTS.md, removing the
  implicit duplication between "running pytest by hand" and "the documented gate."
- `env-init` gives new clones a one-step `.env` bootstrap; before, the
  instruction lived only in the README and `.env.example` header.
- The `build` target covers the missing case of producing a dist artifact.

Negative:
- Anything that scripted against the old Makefile targets breaks. No
  external scripts exist in this repo or its known consumers.

Neutral:
- The `bin/bonsai-server` debug tool promised by ADR-008 ("moves under
  `scripts/dev/` and stays") was never relocated. We drop the promise
  silently — no one has asked for it.


