# Spec-017: Replace the repo Makefile with a dev/release workflow targeting `genesis_worker`

## Overview

The current `Makefile` orchestrates the legacy `bin/` scripts (`catalog.py`,
`build-config.py`, `hf-model.py`, `pi-models.py`, `up`) and writes to repo-root
state files (`recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`,
`pi-models.json`). All of that is gone — the `genesis_worker` package has
replaced it. The Makefile is the last legacy artifact.

This spec replaces it with a small dev/release Makefile that targets the
package: install, test, lint, typecheck, run the UI, bootstrap `.env`, build
dist artifacts, clean.

## Targets

The default goal is `help`.

| Target | Action |
|---|---|
| `help` | Echo the target table. Default. |
| `install` | `uv sync`. |
| `test` | `uv run pytest -q` → `uv run pyright` → `uv run ruff check genesis_worker`. Stops on first failure. This is the gate from AGENTS.md. |
| `test-fast` | `uv run pytest -q` only. Escape hatch for iterating when lint/typecheck are already known-clean. |
| `lint` | `uv run ruff check genesis_worker`. |
| `typecheck` | `uv run pyright`. |
| `ui` | `uv run genesis-worker-ui` (the console script registered in `pyproject.toml`). Foreground; `Ctrl-C` stops Streamlit. |
| `env-init` | Copy `.env.example` to `.env` if `.env` is absent. No-op (with message) if it already exists. Refuses to clobber. |
| `build` | `uv build` — wheel + sdist under `dist/`. |
| `clean` | Remove `dist/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `genesis_worker/**/__pycache__/`. Leaves `.venv/` alone. |

`.PHONY` lists every target above. No target produces a real file with the
target's name, so `.PHONY` is honest and complete.

## Non-targets (deliberate omissions)

- **No CLI wrappers for `genesis_worker/cli/*`.** Users invoke those directly
  via `uv run python -m genesis_worker.cli.{up,catalog,config,pi_models,hf_model}`.
  The old Makefile wrapped each script; that rot is what we're escaping.
- **No `format` target.** `uv run ruff format` is explicitly not used on this
  repo (AGENTS.md); adding the target would invite drift.
- **No `MODELS_ROOT` / `ROOT` variables.** The package resolves its own paths
  from settings; the Makefile has no opinion about where models live.
- **No service-start / service-stop targets.** The daemon is reached via
  `uv run python -m genesis_worker.cli.up start|stop`. Wrapping it in Make
  adds nothing and re-accumulates the same wrapper pattern.

## `env-init` semantics

```make
env-init:
	@if [ -f .env ]; then \
		echo ".env already exists; leaving it alone."; \
	else \
		cp .env.example .env && echo "Created .env from .env.example — edit it before running the worker."; \
	fi
```

- Idempotent: re-running does not destroy edits.
- Refuses to overwrite an existing `.env` (silent data loss is the worst kind).

## Variable conventions

None exposed. `uv run` dispatches the interpreter and dependency set; there
is nothing to override at the Make layer.

## Verification Conditions

- `make` (no args) prints the help table and exits 0.
- `make help` does the same.
- `make install` succeeds on a clean clone.
- `make test` exits non-zero iff any of pytest, pyright, or ruff fails.
- `make test-fast`, `make lint`, `make typecheck` each run only their named tool.
- `make ui` launches Streamlit at the default `http://localhost:8501`; `Ctrl-C` stops it.
- `make env-init` creates `.env` when absent; on a second run, prints "already exists" and exits 0.
- `make env-init` does not modify an existing `.env` (verified via `git status` showing no changes after re-running on a populated `.env`).
- `make build` produces `dist/*.whl` and `dist/*.tar.gz`.
- `make clean` removes all listed artifacts and leaves `.venv/` intact.
- After replacement, the old `bin/`, `MODELS_ROOT`, and per-script wrappers are absent. `grep -E '^(catalog|config|all|up|install-model|pi-)' Makefile` returns no matches.
- `uv run pytest -q`, `uv run pyright`, `uv run ruff check genesis_worker` all still pass (the Makefile change is independent of the package).
