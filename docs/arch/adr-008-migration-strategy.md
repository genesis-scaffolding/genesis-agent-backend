# ADR-008: Migration strategy — `bin/`, `Makefile`, and state files retained through v1

## Title
Migration strategy — `bin/`, `Makefile`, and state files retained through v1

## Context
This machine is currently running `llama-swap` via `bin/up` against a `config.yaml` generated from the current `bin/` scripts. The user is operating this setup live; any change that disrupts the running `llama-swap` is unacceptable during development.

The new package (`genesis_worker/`) will replicate the logic of the existing `bin/` scripts. Two parallel implementations existing simultaneously is acceptable during a transition, but two implementations of the same artifact (`MODEL_CATALOG.yaml`, `config.yaml`, `pi-models.json`) is a foot-gun: bug fixes must remember to land in both.

Additionally, the spec calls for moving state files to XDG dirs. Doing so during development would change `config.yaml`'s path, breaking the running `llama-swap`.

The user's working protocol from the orchestrator repo (`genesis-infrastructure-toolkit/AGENTS.md`) prescribes "do not touch code first," and the worker protocol inherits this discipline: write new code in parallel with the old; only retire the old after the new is validated equivalent.

## Decision

### During v1 development

- `bin/catalog.py`, `bin/build-config.py`, `bin/pi-models.py`, `bin/hf-model.py`, `bin/up`, `bin/bonsai-server` are **not modified**.
- `Makefile` is **not modified**.
- `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json` continue to live in the repo root. New code in `genesis_worker/` writes to new locations by default, but never overwrites the existing files unless explicitly invoked to do so.
- The `Makefile` and `bin/` scripts keep being how the user drives things from the terminal during development.

### Validation gate

Before any `bin/` script is retired, the new module must produce output that is **content-equivalent** to the script's output. Content equivalence means: same model entries, same `cmd` strings, same `proxy`/`ttl`/top-level keys, same JSON shape. Whitespace, key ordering, and quote-style differences are accepted.

Validation is by diff. A validation script (one per retired script) compares:

- `bin/catalog.py` vs `genesis_worker.catalog.build` → `MODEL_CATALOG.{yaml,md}`
- `bin/build-config.py` vs `genesis_worker.services.llama_swap.generate_config` → `config.yaml`
- `bin/pi-models.py` vs `genesis_worker.services.llama_swap.export_pi_config` → `pi-models.json`
- `bin/hf-model.py` → tested at the function level (`group_files`, `classify_path`, `build_hf_command`); end-to-end interactive flow validated manually.
- `bin/up` → tested by running the Python lifecycle module against a container or scratch box.

### Retirement order (post-v1)

`bin/` scripts are retired one at a time after each is validated equivalent:

1. `pi_models.py` (simplest; JSON output is easy to diff)
2. `catalog.py`
3. `build_config.py`
4. `hf_model.py`
5. `up` (last; touches the running tmux session)
6. `bonsai-server` is **not retired** — it's a debug tool, not part of the worker surface. It moves under `scripts/dev/` and stays.

The `Makefile` is updated only as each script is retired.

### State file migration (post-v1)

Migration of `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json` from repo-root to XDG dirs is a **separate, post-v1 phase**. It will be a one-shot `bin/migrate-state` script (or `genesis_worker.cli.migrate_state`) that moves files and updates `Settings` defaults.

Migration is deferred because:

- It changes the running `llama-swap`'s `config.yaml` path. Doing this while llama-swap is up requires downtime.
- It's a one-time operation; no value in shipping it as part of v1.
- ~~The new code's default paths already point at repo-root when those files exist there (ADR-004), so v1 works without migration.~~ **Revised by ADR-009:** `genesis_worker` now writes to `<data_dir>/llama-swap/` and reads recipes bundled inside the plugin, so it never touches the repo-root files. The two live side by side: `bin/` + `Makefile` drive the running llama-swap from repo-root state, and the package operates entirely on XDG paths. Migration is now a cutover of which one you run, not a file move.
- ~~The new code's `Catalog` model carries named `huggingface` / `lmstudio` fields for byte-equivalence with `bin/catalog.py`'s YAML output.~~ **Retired by [ADR-011](adr-011-persistent-catalog-and-source-agnostic-shape.md):** the validation gate is closed, the framework/plugin boundary is the right organizing principle, and `Catalog` is now source-agnostic with a stable content hash.

## Status
Accepted; the state-file rationale is revised by [ADR-009](adr-009-framework-plugin-boundary.md).

`recipes.yaml` now exists twice on purpose: the repo-root copy feeds `bin/`, and
`genesis_worker/services/llama_swap/data/recipes.yaml` ships with the plugin. They are held
in sync by `test_recipes_bundled.py` until `bin/build-config.py` retires.

## Consequences

Positive:
- The running `llama-swap` is never disrupted during v1 development.
- New code is fully validated against the existing `bin/` scripts before the scripts are deleted.
- Retirement is incremental; one foot-gun at a time.
- `bin/bonsai-server` (debug tool) is preserved unchanged.

Negative:
- Two implementations coexist during v1. Confirmed acceptable by the user.
- The retirement phase is post-v1; the user has to do it later. The cost is "remember to come back to it," not "extra work in v1."

Neutral:
- We accept that the `bin/` scripts' PEP 723 inline-script shebangs and the `Makefile`'s direct-`python3` invocations are inconsistent with the new uv-managed package. Resolved by retirement.

## Spec
[spec-003-facade-and-ui](specs/spec-003-facade-and-ui.md) (Phase 10 retirement phase is described here)

## Plan
[plan-003-facade-and-ui](plans/plan-003-facade-and-ui.md) (Phase 10 retirement step list is described here)
