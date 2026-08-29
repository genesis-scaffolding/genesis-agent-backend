# ADR-018: Drop the recipe and vault fixtures; remove dependent tests

## Title
Drop the stale recipe and vault fixtures in the test suite and remove the tests that depend on them.

## Context

After commit `f4e039d` removed the repo-root `recipes.yaml` and the legacy `bin/` scripts, two test fixtures became unrunnable everywhere except the user's own machine:

- `genesis_worker/tests/test_config_emit.py::real_recipes` loads `repo_root() / "recipes.yaml"`, which no longer exists.
- `genesis_worker/tests/test_config_emit.py::real_catalog` hardcodes `Path("/home/gentran1991/Data2/models")`, which only exists on this machine.

The new `make test` target (ADR-017) surfaced six dependent test errors plus one unrelated failure that had been silently skipped or relied on a hardcoded path:

- `test_load_real_repo_recipes` (test_recipes.py) — directly tests the deleted location.
- `test_make_entry_id_collision_suffixing`
- `test_build_config_emits_one_entry_per_recipe`
- `test_overrides_change_emitted_cmd`
- `test_emitted_yaml_parses_back`
- `test_no_extra_yaml_keys`
- `test_detect_file_sets_returns_one_per_main` (passes here because the vault exists; would fail on any other machine).

The Makefile rewrite exposed this breakage; it did not cause it. Fixing the fixtures properly (synthetic catalog + bundled recipes) is a real refactor and would still leave `make test` green only on machines with non-trivial fixtures. The tests' coverage value is integration-only: `build_config` against real-shape data. Unit coverage of the build pipeline (`test_generate_config.py`, `test_config_emit.py`'s remaining tests) is already strong.

## Decision

We will delete the six failing tests plus the seventh (`test_detect_file_sets_returns_one_per_main`) that depends on the same hardcoded vault. We will delete the `real_catalog` and `real_recipes` fixtures and trim the now-dead imports from `test_config_emit.py` and `test_recipes.py`.

We will also drop two unused imports (`_resolve_chat_template_file`, `cmd_from_evaluated`) from `test_generate_config.py` so `make lint` is clean.

## Status
Accepted.

## Consequences

Positive:
- `make test` becomes green unconditionally. The gate stops lying about coverage it cannot actually run.
- The test suite no longer depends on a hardcoded path that only exists on the author's machine.
- One small lint fix rides along.

Negative:
- Loss of end-to-end coverage that combined a real catalog with real recipes to assert the emitted `config.yaml` shape. `test_make_display_name_strips_gguf_and_appends_variant` and the `write_config` round-trip tests remain; pure-unit tests of the recipe resolver and build pipeline remain.

Neutral:
- Anyone wanting that integration coverage back will need to either (a) ship a synthetic `Catalog` fixture (medium-sized refactor) or (b) gate the test on `GENESIS_VAULT_PATH` and accept honest skip semantics.


