# ADR-015: Per-piece llama-swap config entries

## Title

Per-piece llama-swap config entries

## Context

A catalog `ModelEntry` for a HuggingFace model can contain multiple GGUF quantization files (e.g. `Q4_K_M`, `Q6_K`, `F16`). The current config generator collapses all of them into one llama-swap entry, deriving the display name and YAML key from the model-level `native_id`. The quant is only discoverable by drilling into the catalog UI and inspecting the file list. Additionally, if a user downloads multiple quantizations of the same model, only one makes it into the llama-swap config.

The user wants each GGUF piece to appear as a distinct entry in the llama-swap config, with the quant visible in the entry name.

## Decision

We will expand one catalog entry into N llama-swap entries (one per main GGUF piece) at config-generation time. Recipe matching stays on the model-level name to keep recipes.yaml stable. Only the entry name and YAML key are derived from the piece filename.

The change is isolated to `generate_config.py`:
- `detect_files` is replaced by `detect_file_sets`, returning `list[DetectedFileSet]` (one per main piece) instead of a single `DetectedFiles`.
- `walk_models` gains an inner loop over file sets before the recipe loop.
- `make_entry_id` and `make_display_name` receive the piece filename instead of the model name.
- The catalog, catalog persistence, sources, and UI are unchanged.

## Status

Accepted.



## Consequences

**Positive:**
- Quant is visible in llama-swap config entry names without digging into file lists.
- Multiple quantizations of the same model each get their own llama-swap entry.
- Recipe matching remains model-level — no changes needed to `recipes.yaml`.

**Negative:**
- `overrides.yaml` keys for multi-GGUF entries change from model-level to piece-level keys. Existing overrides for single-GGUF models continue to work since the piece filename equals the model name for those.
- An entry with N GGUF files × M matched recipes produces N×M llama-swap entries. With current recipes this is acceptable; if it becomes problematic, a future ADR can add quant-aware recipe filtering.

**Neutral:**
- The catalog schema is unchanged; the expansion happens only at config generation.
- `delete_model` continues to work on model-level entries.
