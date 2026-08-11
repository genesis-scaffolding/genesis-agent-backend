# Plan 001: Core architecture

Implements [spec-001](../specs/spec-001-core-architecture.md).

## Working rules

- Branch: `feature/genesis-worker-core` from `master`.
- Dependency changes use `uv` only. Never hand-edit `pyproject.toml` after the initial `uv init`.
- `bin/`, `Makefile`, `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json` are not modified. The running llama-swap must keep serving during this entire plan.
- Commit message: one line. No body. Wait for user approval before committing.

## File-by-file

1. **Create branch** `feature/genesis-worker-core`.
2. **`uv init`** at the repo root. Creates `pyproject.toml`, `uv.lock`, `.python-version`, `main.py`, `hello.py`, `.gitignore`, `README.md`.
3. **Remove** `main.py` and `hello.py` (we don't need the default module; the worker IS the project).
4. **Set `.python-version`** to `3.11` (matches orchestrator and the existing PEP 723 scripts).
5. **`uv add pydantic pydantic-settings pyyaml psutil pynvml`** (runtime deps).
6. **`uv add --dev pytest ruff pyright`** (dev deps).
7. **`uv sync`** — verify `.venv` is created.
8. **Create directory structure:**
   ```bash
   mkdir -p genesis_worker/{sources,services/llama_swap,catalog,tests}
   touch genesis_worker/__init__.py
   touch genesis_worker/sources/__init__.py
   touch genesis_worker/services/__init__.py
   touch genesis_worker/services/llama_swap/__init__.py
   touch genesis_worker/catalog/__init__.py
   ```
9. **Write `genesis_worker/paths.py`** per spec-001.
10. **Write `genesis_worker/settings.py`** per spec-001.
11. **Write `genesis_worker/sources/_base.py`** per spec-001.
12. **Write `genesis_worker/sources/_registry.py`** per spec-001.
13. **Write `genesis_worker/sources/huggingface.py`** — lift walker from `bin/catalog.py:walk_huggingface`. Keep the constants (`COMPONENT_DIRS`, `WEIGHT_EXTS`, `SKIP_FILENAMES`) verbatim. Convert dict outputs to `DiscoveredModel` dataclasses. **Do not modify** `bin/catalog.py`.
14. **Write `genesis_worker/sources/lmstudio.py`** — lift walker from `bin/catalog.py:walk_lmstudio` the same way.
15. **Write `genesis_worker/catalog/schema.py`** per spec-001.
16. **Write `genesis_worker/catalog/build.py`** per spec-001.
17. **Write `genesis_worker/services/_base.py`** — protocols for the service registry, but defer capabilities/status/result types to plan-002 (only the minimum needed for this plan: `register_service` decorator + `all_services()`).
18. **Write `genesis_worker/services/_registry.py`** — mirror of `sources/_registry.py`.
19. **Write `genesis_worker/services/llama_swap/recipes.py`** per spec-001.
20. **Write `genesis_worker/services/llama_swap/overrides.py`** per spec-001.
21. **Write `genesis_worker/services/llama_swap/generate_config.py`** — lift `_opt`, `normalize`, `get_matching_recipes`, `_is_llm_candidate`, `detect_files`, `_SAMPLING_FLAGS`, `build_cmd`, `make_entry_id`, `make_display_name`, `build_entry` from `bin/build-config.py`. Replace the hand-rolled YAML emitter at the end of `emit_yaml` with `yaml.safe_dump`. Add `resolved_from` annotation.
22. **Write `genesis_worker/tests/test_paths.py`** per spec-001.
23. **Write `genesis_worker/tests/test_settings.py`** per spec-001.
24. **Write `genesis_worker/tests/test_sources_registry.py`** per spec-001.
25. **Write `genesis_worker/tests/test_sources_huggingface.py`** — build a fixture tree under `tmp_path` (one `models--org--name` with `refs/main`, `snapshots/<sha>/`, one main `.gguf`, one mmproj); walk; assert entry count + pieces + total_bytes.
26. **Write `genesis_worker/tests/test_sources_lmstudio.py`** — similar fixture.
27. **Write `genesis_worker/tests/test_catalog_build.py`** per spec-001.
28. **Write `genesis_worker/tests/test_recipes.py`** — load current `recipes.yaml`; resolve a battery of model names per spec-001 verification list.
29. **Write `genesis_worker/tests/test_overrides.py`** per spec-001.
30. **Write `genesis_worker/tests/test_config_emit.py`** — per spec-001. Include a real-catalog regression test that loads `MODEL_CATALOG.yaml` and `recipes.yaml`, builds a `config.yaml` to a temp file, and diffs `cmd` strings against the current `config.yaml` (ignoring whitespace).
31. **`uv run pytest genesis_worker/tests/`** — must pass.
32. **`uv run ruff check`** — must exit 0.
33. **`uv run pyright`** — must exit 0 (or accept known informational items; do not introduce new errors).
34. **Smoke-test the public surfaces** per spec-001 verification steps 3–7. Confirm `config.yaml` on disk is **unchanged** (the new code wrote only to temp files).
35. **`make all`** — must still pass; `bin/catalog.py` and `bin/build-config.py` still produce their original output.
36. **Confirm the running llama-swap** is still serving on port 8080 with the same model list (`curl -s http://127.0.0.1:8080/v1/models | python3 -m json.tool`).
37. **Wait for user approval**, then commit (one-line message) on the branch.

## Notes

- The constants and helpers lifted from `bin/catalog.py` and `bin/build-config.py` are the only code that's duplicated during this plan. We accept the duplication in v1; Phase 10 retirement replaces `bin/` with thin shims that call into these new modules.
- `_opt` and the recipe resolver are sensitive to the order of cascades (recipe → default). The test `test_recipes.py`'s battery covers the cases that mattered on the real `recipes.yaml`. If a future recipe is added, the test should grow.
- The `config.yaml` write-if-changed test asserts mtime preservation. Use `os.stat(path).st_mtime_ns` for sub-second precision.
- Don't add new entries to `MODEL_CATALOG.yaml` or `config.yaml` during this plan. The new code only writes to `tmp_path` test fixtures or to XDG-defaulted paths (which are not used during validation; we pass explicit temp paths to the test).
