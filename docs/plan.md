# Genesis Worker — Roadmap

This document is the entry point for the Genesis Worker rollout. It links to the architectural decisions (ADRs), implementation specs, and file-by-file plans.

For the original product spec (Genesis Worker feature scope, related Linear tasks, acceptance criteria), see the upstream spec — not yet committed to this repo.

## What this is

We are turning the current `my-agent-backend` repo into the **Genesis Worker** half of the Genesis Infrastructure Toolkit. The worker runs on each machine in the fleet and provides a Streamlit UI (reachable from a phone on Tailscale) for managing local AI infrastructure — llama-swap today, ComfyUI / AIToolkit / vLLM later.

The current repo works: `bin/` scripts + `Makefile` produce a `config.yaml` that the running `llama-swap` consumes. The plan is to introduce structure — a Python package, a facade, pluggable sources and services — **without breaking anything currently running**.

## Architectural decisions (ADRs)

All architectural decisions live in `docs/arch/`. Each decision is a Nygard-format ADR with context, decision, status, and consequences, plus links to its spec and plan.

| # | ADR | Summary |
|---|-----|---------|
| 003 | [Genesis Worker architecture](arch/adr-003-genesis-worker-architecture.md) | Single `GenesisWorker` facade; `ModelSource` and `InferenceService` as pluggable protocols with in-tree registries; capability-driven UI; multi-service peers. |
| 004 | [Settings layout (XDG, nested)](arch/adr-004-settings-layout-xdg-nested.md) | XDG-compliant paths; per-source and per-service settings nested under a top-level `Settings`; v1 backwards-compat via repo-root fallback. |
| 005 | [HF acquisition via `huggingface_hub`](arch/adr-005-hf-acquisition-via-huggingface-hub.md) | Use the library directly; `uvx hf download` subprocess is dropped; acquire flows are a session protocol, not source-specific UI. |
| 006 | [PyYAML replaces hand-rolled YAML](arch/adr-006-pyyaml-replaces-hand-rolled.md) | One consistent YAML library across the codebase; byte-level diffs accepted; content must be equivalent. |
| 007 | [Overrides in `overrides.yaml`; no SQLite in v1](arch/adr-007-overrides-yaml-no-sqlite.md) | Declarative per-model overrides; defer SQLite. |
| 008 | [Migration strategy](arch/adr-008-migration-strategy.md) | `bin/`, `Makefile`, and state files retained through v1; retired post-v1 after each new module is validated equivalent. |
| 009 | [Framework / plugin boundary](arch/adr-009-framework-plugin-boundary.md) | `contracts` is the only shared surface; plugins own their options schema; framework passes resolved contexts; ABCs replace Protocols. Supersedes parts of 003 and 004. |

## Implementation specs

| # | Spec | Phases covered | What it ships |
|---|------|----------------|---------------|
| 001 | [Core architecture](arch/specs/spec-001-core-architecture.md) | 0–4 | `genesis_worker` package skeleton; sources (HF, LM Studio); catalog service; recipes schema + resolver; overrides store; `config.yaml` generation with write-if-changed. |
| 002 | [Services and acquire flows](arch/specs/spec-002-services-and-acquire.md) | 5–7 | `llama-swap` inference service (lifecycle + pi-models export); `AcquireSession` protocol; HuggingFace implementation using `huggingface_hub`. |
| 003 | [Facade, CLI, and Streamlit app](arch/specs/spec-003-facade-and-ui.md) | 8–9 | `GenesisWorker` facade; thin CLI wrappers; Streamlit multi-page app (dashboard, catalog, acquire, config editor, recipes view, pi export). |

## File-by-file plans

| # | Plan | Branch |
|---|------|--------|
| 001 | [Core architecture](arch/plans/plan-001-core-architecture.md) | `feature/genesis-worker-core` |
| 002 | [Services and acquire flows](arch/plans/plan-002-services-and-acquire.md) | `feature/genesis-worker-services` |
| 003 | [Facade, CLI, and Streamlit app](arch/plans/plan-003-facade-and-ui.md) | `feature/genesis-worker-ui` |

## Implementation phases (roadmap)

Phases per the master plan; the work is sliced into the three plans above.

| Phase | Goal | In plan |
|-------|------|---------|
| 0 | Foundations (pyproject, settings, paths) | plan-001 |
| 1 | Source protocol + walkers (HF, LM Studio) | plan-001 |
| 2 | Catalog service + pydantic schema | plan-001 |
| 3 | Recipe schema + resolver | plan-001 |
| 4 | Overrides + config emit (PyYAML) | plan-001 |
| 5 | Llama-swap lifecycle + `InferenceService` | plan-002 |
| 6 | Pi-models export (PyYAML parse) | plan-002 |
| 7 | HF acquire session (`huggingface_hub`) | plan-002 |
| 8 | Facade + thin CLI | plan-003 |
| 9 | Streamlit app (multi-page, 0.0.0.0 bind) | plan-003 |
| 10 | **Post-v1:** retire `bin/` scripts one at a time | (separate, later) |
| 11 | **Post-v1:** migrate state files to XDG dirs | (separate, later) |

## Working protocol

This repo follows the same working protocol as the orchestrator repo (`genesis-infrastructure-toolkit/AGENTS.md`):

- Dependency changes use `uv` only (`uv add`, `uv add --dev`). Never hand-edit `pyproject.toml` after `uv init`.
- New work happens on a branch off `master`. Branch names: `feature/<kebab>` or `fix/<kebab>`. No direct commits to `master`.
- Before asking for approval, the relevant `uv run pytest` and `uv run ruff check` / `uv run pyright` must pass.
- Commit message: one line. No body. Wait for user verification before committing.
- Merge with `--no-ff`.

## Hard constraints (carried into every plan)

- `bin/`, `Makefile`, `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json` are **not modified** during v1 development.
- The running `llama-swap` on `:8080` (started by the existing `bin/up`) must keep serving during every step of every plan.
- Validation gates are content-equivalence diffs against current artifacts.
- Streamlit binds to `0.0.0.0` (Tailscale-side firewall does the rest).

## What's out of scope for v1

- Migration of state files to XDG dirs (Phase 11, post-v1).
- Retirement of `bin/` scripts (Phase 10, post-v1).
- New sources (ModelScope, Civitai, etc.).
- New inference services (ComfyUI, AIToolkit, vLLM).
- VRAM conflict enforcement (always-enabled Start buttons; crashes acceptable).
- HTTP API on the worker.
- Authentication / multi-user / session locking.
- SQLite / durable acquire sessions.
- Formal unit-test suite beyond smoke / parity tests.
- Streamlit UX refinements beyond canonical patterns.
- The eventual monorepo merge into `genesis-infrastructure-toolkit/`.
