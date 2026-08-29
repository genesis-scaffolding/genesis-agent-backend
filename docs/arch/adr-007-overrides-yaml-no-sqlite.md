# ADR-007: Per-model overrides in `overrides.yaml`; no SQLite in v1

## Title
Per-model overrides in `overrides.yaml`; no SQLite in v1

## Context
The Config Editor page (worker spec, feature #3) needs to let the user toggle a per-model override on each recipe-derived parameter (sampling knobs, ctx_min, kv_cache, mmproj_offload, reasoning_budget, chat_template_file, chat_template_kwargs, etc.). When a toggle is OFF, the value falls back to the recipe. When ON, the user's value is used.

This is **persistent state** that doesn't exist today. It needs a home.

Two candidates:

1. **`overrides.yaml`** — a flat YAML file keyed by entry-id, with a `{field: value}` dict per entry. Declarative, merge-friendly, easy to read and edit by hand if needed.
2. **SQLite** — a small table with `(entry_id, field, value, updated_at)` rows. Better for many writers, ad-hoc queries, partial-update transactions.

The orchestrator's spec mentions SQLite for its own persistence needs (fleet inventory, deployment history). The worker's spec also mentions SQLite ("defer Postgres; single-writer is fine"). But the spec doesn't say **what** is in SQLite, and there's no operational reason (yet) to choose SQLite over a flat file.

Single-user. Single-writer. Small dataset (≤20 models × ≤15 fields). No need for ad-hoc queries. No migration history needed.

## Decision

We will use `overrides.yaml` for per-model user overrides in v1. SQLite is not introduced.

Schema:

```yaml
# overrides.yaml — user overrides on top of recipe defaults.
# Keyed by llama-swap entry-id. Fields not present here fall back to the
# matched recipe (or `default` if no recipe matched).
entries:
  rocinante-xl-16b-v1-gguf:
    sampling:
      temp: 0.6
      top_k: 30
  qwen3-6-35b-a3b-gguf-thinking:
    reasoning_budget: 8192
```

Merging precedence (lowest → highest) at config-build time:

1. Recipe's value
2. `default` recipe's value (cascade from `default`)
3. `overrides.yaml` value
4. CLI `--binary` (binary path only)

The build pipeline reads `overrides.yaml` if present; missing file = empty store.

If the user wants to clear an override, they delete the field from `overrides.yaml`. There is no need for an explicit "override tombstone" — absence means fall-back.

## Status
Accepted

## Consequences

Positive:
- Declarative, readable, hand-editable.
- No schema migration story to design.
- Easy to merge recipe changes via git (one diff per override change).
- v2 can migrate to SQLite without changing the build pipeline's API — only the storage backend.

Negative:
- No query layer. If we ever want "show me all overrides across entries," we read the file. Acceptable; dataset is tiny.
- No atomic partial updates — if two writers exist (they don't in v1), last-write-wins. Acceptable.

Neutral:
- One more YAML file in the layout; not a problem.


