# Plan-018: Drop stale recipe and vault fixtures; remove dependent tests

## Step 1 — `test_recipes.py`: delete `test_load_real_repo_recipes`

**File:** `genesis_worker/tests/test_recipes.py`

Delete the function `test_load_real_repo_recipes` (lines 154-157). Remove the `from genesis_worker.paths import repo_root` import (line 10) since nothing else uses it.

## Step 2 — `test_config_emit.py`: delete fixtures and dependent tests

**File:** `genesis_worker/tests/test_config_emit.py`

1. Delete `REPO_ROOT` and `BUILD_OPTIONS` module-level constants.
2. Delete `real_catalog` and `real_recipes` fixtures.
3. Delete the six tests listed in spec-018.
4. Replace the import block with the trimmed version from spec-018.
5. Update the module docstring to drop the now-untrue line about removed coverage.

## Step 3 — `test_generate_config.py`: drop two unused imports

**File:** `genesis_worker/tests/test_generate_config.py`

Remove `_resolve_chat_template_file` and `cmd_from_evaluated` from the import block at line 21-22.

## Step 4 — Verify

```sh
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
make test
```

All four must pass.
