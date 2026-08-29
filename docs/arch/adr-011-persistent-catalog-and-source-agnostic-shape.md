# ADR-011: Persistent catalog and source-agnostic shape

## Title

Persistent catalog with stable `generated_at` and source-agnostic `Catalog` shape.

## Context

Two related correctness problems with the in-memory catalog:

1. **`generated_at` rotates on every rescan.** `build_catalog` stamps `datetime.now(UTC)` on every call (`genesis_worker/utils/catalog_utils.py`). A rescan that observes no change to the vault still produces a new timestamp. The llama-swap config editor's staleness check (`is_config_stale` in `genesis_worker/services/llama_swap/generate_config.py:780`) compares the catalog's `generated_at` against the `generated_at` embedded in `config.yaml`. So every streamlit startup, and every explicit "Rescan" click, makes the config look stale — even when nothing has changed.

2. **The catalog isn't persisted.** It lives only in `GenesisWorker._catalog_cache` (`genesis_worker/facade.py:42`). After an HF acquire completes, the new file lands on disk but the cached catalog doesn't see it until the user clicks "Rescan catalog" (or streamlit restarts and the cache is empty). The button's tooltip also lies — it says "writes to `~/.cache/genesis-worker/`" though rescan writes nothing anywhere.

A separate concern that has been waiting on the validation gate to clear:

3. **`Catalog` hardcodes source names.** The pydantic model declares `huggingface: list[ModelEntry]` and `lmstudio: list[ModelEntry]` as named fields (`genesis_worker/contracts/catalog.py:56-57`). The reason was historical — `bin/catalog.py` writes YAML with those exact top-level keys, and ADR-008 wanted byte-equivalence for the validation gate. ADR-009 then established the framework/plugin boundary, which says the framework must not know source names in advance. Adding a new source (e.g., a future `comfyui` source for image models) currently requires touching `Catalog`, `_build_catalog`, and tests — a violation of the boundary.

The boundary's `Catalog.by_source()` accessor (`genesis_worker/contracts/catalog.py:60-65`) already abstracts over the named fields by introspecting `model_fields` — but it does so *because* the framework has hardcoded them as fields. The accessor itself is sound; the model shape isn't.

## Decision

### Persist the catalog

The catalog becomes a real on-disk artifact at `state_dir/catalog.json` (default `~/.local/state/genesis-worker/catalog.json`). `CatalogService.rescan()` writes it; `GenesisWorker.catalog()` reads it on startup.

Format: JSON (pydantic `model_dump_json` / `model_validate_json`). The legacy `MODEL_CATALOG.yaml` was for human inspection; the new file is for the framework's own change-detection.

Write semantics: text-diff skip — `save_catalog` reads the existing file (if any), compares to the would-be content, and skips the write when identical. Atomic via `os.replace()` on a sibling temp file.

### Stable `generated_at`

`Catalog` carries a `content_hash: str` field. `_build_catalog` computes it deterministically over `(source, name, total_bytes, sorted pieces)` across all entries. `rescan()` loads the persisted catalog (if any) and reuses its `generated_at` when the persisted `content_hash` equals the new one. When they differ, the new `generated_at = now()` wins and the file is rewritten.

The persisted file is the durable source of stability. Across streamlit restarts: the file is loaded, `content_hash` matches whatever's on disk, `generated_at` is the timestamp from the last actual change. llama-swap's `config.yaml`, if built from the same content, has the same timestamp — `is_config_stale` is False.

`content_hash` excludes `directory`, `notes`, and `extra` per-entry fields (those don't affect what llama-swap would generate) and includes piece-level `role` / `filename` / `bytes` so file additions and deletions flip the hash.

### Schema versioning

`schema_version: int = 1` is the first field on `Catalog`. `load_catalog` rejects mismatched versions and rebuilds. Future shape changes bump the version and write a one-shot migration; today there's nothing to migrate.

### Source-agnostic `Catalog` shape

`Catalog` becomes:

```python
class Catalog(BaseModel):
    schema_version: int = 1
    root: str
    generated_at: str
    content_hash: str
    entries: list[ModelEntry] = Field(default_factory=list)

    def by_source(self) -> dict[str, list[ModelEntry]]:
        out: dict[str, list[ModelEntry]] = {}
        for entry in self.entries:
            out.setdefault(entry.source, []).append(entry)
        return out
```

`ModelEntry.source` is the string set by the source plugin's `name`. The framework never inspects this string for routing; consumers (e.g., llama-swap's `_is_llm_candidate` filter for HF GGUF presence) may use it as a per-source content filter. Adding a source is purely additive: drop a subpackage under `sources/`. No framework edit.

### Short-source label

`genesis_worker/services/llama_swap/generate_config.py:208` currently does `src_short = "hf" if source == "huggingface" else "lms"`. Replace with a generic `short_source_label(source: str) -> str` helper that lowercases the source, strips non-alphanumerics, and takes the first three characters. `"huggingface" → "hug"`, `"lmstudio" → "lms"`, `"comfyui" → "com"`. Deterministic, no per-source special cases. The historical hand-chosen labels (`"hf"`, `"lms"`) are intentionally not preserved — entry IDs regenerate on every config rebuild, and the new rule works uniformly for any future source.

`_is_llm_candidate`'s `if source == "huggingface"` check stays — it's a per-source content filter (HF entries must contain `.gguf`), not a registry-of-names.

### Catalog page auto-refresh

`genesis_worker/ui/catalog.py` tracks the set of acquire-session IDs that have hit `complete` since last render. On each render, if new completions are seen, it calls `worker.rescan_catalog()` and toasts "Auto-refreshed after download — N models now". The manual "↻ Rescan catalog" button stays as a fallback.

The misleading comment about `~/.cache/genesis-worker/` writes is removed.

## Status

Accepted.

## Consequences

**Positive**

- The catalog has a stable identity across rescans. `generated_at` reflects *when the world last changed*, not *when streamlit last looked*. llama-swap's "config stale" warning becomes a true signal.
- The catalog persists across streamlit restarts. A user returning to the dashboard sees the same state they left.
- After an HF download completes, the Catalog page auto-refreshes. New users don't have to learn about the Rescan button.
- Adding a new source plugin no longer touches the framework. `Catalog` is shape-agnostic over source names.
- Atomic write prevents two streamlit sessions from truncating each other's catalog on startup.

**Negative**

- A new on-disk state file. ADR-008 deferred state-file migration to a post-v1 phase; this adds one file (`catalog.json`) that lives at the new XDG path, so there's nothing to migrate *from*. The post-v1 phase should still retire the repo-root `MODEL_CATALOG.yaml` and `bin/catalog.py` per ADR-008's plan, but they were never written by the new code; only the framework's new file is affected.
- Tests that referenced `cat.huggingface` / `cat.lmstudio` directly need to switch to `cat.by_source()["huggingface"]` etc. Mechanical, but a deliberate edit.
- The `generated_at` field is no longer "when this object was built" — it's "when the world last changed". A reader expecting the former may be surprised. The field name doesn't lie, but the implicit contract changes.

**Neutral**

- JSON instead of YAML. The legacy `MODEL_CATALOG.yaml` was YAML; the new file is JSON. Users don't open this file by hand, so the format choice is invisible.
- `schema_version: 1` is forward-looking. Nothing to migrate today.
- The `Catalog.by_source()` accessor still exists and is the right way for consumers to iterate. Its internal implementation changed; its contract didn't.

