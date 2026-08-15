# Spec-010: Per-piece llama-swap config entries

## Overview

Each GGUF quantization file (main piece) in a catalog entry gets its own llama-swap config entry. Recipe matching remains on the model-level name; only the entry name and YAML key are derived from the piece filename.

## Background

A catalog `ModelEntry` for a HuggingFace model like `meta-llama/Llama-3.1-8B-Instruct` can contain multiple GGUF files (e.g. `Q4_K_M`, `Q6_K`, `F16`). The current config generator collapses all of them into one llama-swap entry, using only the largest GGUF. The quant is not visible in the llama-swap UI without drilling into the catalog page.

## Design

### `detect_files` → `detect_file_sets`

Rename `detect_files(entry: ModelEntry) -> DetectedFiles` to `detect_file_sets(entry: ModelEntry) -> list[DetectedFiles]`.

- Each item in the list has the same `mmproj`, `draft`, `is_mtp`, and `mmproj_bytes` (derived from entry-level pieces).
- Each item has its own `main` (one GGUF path) and `weight_bytes` (that piece's bytes).
- If the entry has one main piece, the list has one element — no change in behavior for single-quant models.
- The largest-main-pick logic is removed; we iterate over every main piece instead.

`DetectedFiles.weight_bytes` is updated to `piece_bytes: int` to make the per-piece nature explicit. All call sites that use `weight_bytes` continue to work unchanged.

### `walk_models` — add inner loop over file sets

Before the `for recipe in resolved.matched` loop, add:

```python
for files in detect_file_sets(entry):
    for recipe in resolved.matched:
        entry_id = make_entry_id(
            files.main.name,   # piece filename, not entry.name
            recipe,
            ...
        )
        ...
```

Recipe resolution (`recipes.resolve(entry.name)`) is unchanged — it still runs on the model-level name. This keeps recipe matching stable and avoids changing the recipes.yaml surface.

### `make_entry_id` and `make_display_name`

Both already accept a string `name` parameter. Call sites in `walk_models` now pass `files.main.name` (the piece filename, e.g. `"Llama-3.1-8B-Instruct-Q4_K_M.gguf"`) instead of `entry.name`.

- `make_entry_id` sanitizes the GGUF filename → YAML key: `llama-3-1-8b-instruct-q4-k-m.gguf`
- `make_display_name` strips the `.gguf` extension → display name: `"Llama-3.1-8B-Instruct-Q4_K_M"`

### Upstream contract unchanged

- `build_config` and `evaluate_all` call `walk_models` and produce `list[tuple[str, EvaluatedConfig]]` / `dict[str, EvaluatedConfig]` — same return types.
- `regenerate_config`, `evaluate_model_config`, and the config editor UI consume these unchanged.
- `DetectedFiles` is renamed to `DetectedFileSet` to avoid confusion with the old single-result function name. The old `detect_files` function is removed (no remaining call sites).

## Verification conditions

1. A catalog entry with 3 GGUF files produces exactly 3 llama-swap config entries.
2. Each entry's YAML key contains the quant suffix (e.g. `q4-k-m`, `q6-k`).
3. Each entry's display name contains the quant suffix.
4. Each entry's `cmd` references only the GGUF file for that piece.
5. An entry with a single GGUF file produces exactly 1 llama-swap config entry (no regression).
6. Recipe resolution still uses the model-level name — a recipe matching `"Qwen3.2-4B"` still matches `Qwen3.2-4B-Instruct-Q4_K_M.gguf`.
7. `overrides.yaml` keys for existing single-quant models continue to work (the piece filename equals the entry name for those models).
8. All existing tests pass.
