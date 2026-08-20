# Spec-018: Drop stale recipe and vault fixtures; remove dependent tests

## Overview

Delete six tests plus one fixture-tied test that depend on a hardcoded path to the user's model vault or on the now-removed repo-root `recipes.yaml`. Trim dead imports. Drop two unused imports in `test_generate_config.py` so `make lint` is clean.

## Changes

### `genesis_worker/tests/test_recipes.py`

Delete the function `test_load_real_repo_recipes`.

Remove `from genesis_worker.paths import repo_root` — no longer used after the deletion.

### `genesis_worker/tests/test_config_emit.py`

Delete:

- Module-level constant `REPO_ROOT`.
- Module-level constant `BUILD_OPTIONS`.
- Fixture `real_catalog`.
- Fixture `real_recipes`.
- Tests:
  - `test_detect_file_sets_returns_one_per_main`
  - `test_make_entry_id_collision_suffixing`
  - `test_build_config_emits_one_entry_per_recipe`
  - `test_overrides_change_emitted_cmd`
  - `test_emitted_yaml_parses_back`
  - `test_no_extra_yaml_keys`

Trim imports to those still used by the remaining tests (`test_short_source_label_*`, `test_make_display_name_strips_gguf_and_appends_variant`, `test_write_config_writes_when_changed`, `test_write_config_preserves_mtime_on_noop`):

```python
from __future__ import annotations

from pathlib import Path

from genesis_worker.services.llama_swap.generate_config import (
    make_display_name,
    short_source_label,
    write_config,
)
from genesis_worker.services.llama_swap.recipes import Recipe
```

Update the module docstring to drop the line about removed coverage.

### `genesis_worker/tests/test_generate_config.py`

Remove the two unused imports flagged by ruff:

```python
_resolve_chat_template_file,
cmd_from_evaluated,
```

The docstring's reference to `cmd_from_evaluated` is fine to leave (it's prose).

## Verification Conditions

- `uv run pytest -q` passes with zero failures and zero errors.
- `uv run pyright` passes.
- `uv run ruff check genesis_worker` passes.
- `make test` is green end-to-end.
- `grep -rn 'real_catalog\|real_recipes\|repo_root.*recipes' genesis_worker/tests/` returns no matches.
