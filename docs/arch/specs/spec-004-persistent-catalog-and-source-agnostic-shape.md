# Spec 004: Persistent catalog and source-agnostic shape

Implements [ADR-011](../adr-011-persistent-catalog-and-source-agnostic-shape.md) atop [ADR-009](../adr-009-framework-plugin-boundary.md) (framework/plugin boundary) and [ADR-008](../adr-008-migration-strategy.md) (catalog's legacy field names are now retired).

## Goal

Two changes that have been waiting on the validation gate to clear:

1. The catalog is a real on-disk artifact, like `MODEL_CATALOG.yaml` was. It carries a stable identity (`content_hash`) so `generated_at` only advances when the world changes. Once persisted, streamlit restarts don't reseat the timestamp; llama-swap's config stops showing "stale" warnings when nothing actually changed.

2. The framework stops knowing source names in advance. `Catalog` is no longer a pydantic model with named `huggingface` / `lmstudio` fields — it's `entries: list[ModelEntry]` with each entry carrying its own `source` string. Adding a new source is purely additive: drop a subpackage under `sources/`, no framework edits.

End state:

- `state_dir/catalog.json` is written by `CatalogService.rescan()` and loaded on startup.
- Same on-disk state → same `generated_at`, same `content_hash`, no spurious config staleness.
- New file in the vault → `content_hash` changes → `generated_at` advances → `catalog.json` rewrites → llama-swap `config.yaml` correctly shows stale once, regenerates, and is stable again.
- HF acquire completes → Catalog page auto-rescans and toasts the user; the manual "↻ Rescan" button remains as a fallback.

## Architectural alignment

- **ADR-009** — framework/plugin boundary. The framework talks to source plugins through `walk()`, never through hardcoded names.
- **ADR-008** — the legacy field-name rationale (ADR-008's "Catalog shape" footnote) is now retired. The rest of ADR-008 stands: `bin/`, `Makefile`, repo-root state files are not modified.
- **ADR-004** — `state_dir` is `~/.local/state/genesis-worker/` by default. The catalog lives there, not in the repo root.
- **ADR-010** — per-plugin UI pages. The Catalog page reads through `by_source()` and through `SourceInfo.display_name`; it never iterates named source fields.

## New `Catalog` shape

```python
# genesis_worker/contracts/catalog.py

class Catalog(BaseModel):
    schema_version: int = 1
    root: str
    generated_at: str
    content_hash: str
    entries: list[ModelEntry] = Field(default_factory=list)

    def by_source(self) -> dict[str, list[ModelEntry]]:
        """Group entries by entry.source. Stable order: insertion order of source names."""
        out: dict[str, list[ModelEntry]] = {}
        for entry in self.entries:
            out.setdefault(entry.source, []).append(entry)
        return out
```

`ModelEntry.source` is the string set by the source plugin's `name` (e.g., `"huggingface"`, `"lmstudio"`, `"comfyui"` when added later). The framework never inspects this string for routing; consumers like llama-swap's `_is_llm_candidate` may use it as a per-source filter, but that's a content-aware decision, not a registry-of-names decision.

## Persistence

### File location and format

- Path: `state_dir / "catalog.json"`. `state_dir` is `PathsSettings.state_dir`, defaulted by `xdg_path("STATE", ".local/state", XDG_BASE)` per ADR-004 → `~/.local/state/genesis-worker/catalog.json`.
- Format: JSON (pydantic `model_dump_json(indent=2)` / `model_validate_json()`). Two reasons:
  1. Pydantic round-trips cleanly; no quoting or escaping surprises.
  2. The legacy `MODEL_CATALOG.yaml` was for human inspection. The new file is for the framework's own change-detection. Users browse models in the Streamlit UI; nobody opens `catalog.json` in vim.

### Atomic write

`save_catalog(path, catalog)` writes to `path.with_suffix(".json.tmp")` in the same directory, then `os.replace()`s. Two streamlit sessions racing on startup don't truncate each other. On POSIX this is atomic; on Windows it's effectively atomic since `os.replace` is a single syscall.

### No-op skip

```python
def save_catalog(path: Path, catalog: Catalog) -> bool:
    text = catalog.model_dump_json(indent=2)
    try:
        if path.read_text() == text:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return True
```

Returns `True` iff a write happened. `CatalogService.rescan()` and `GenesisWorker.catalog()` only update in-memory state when a write happened — but more importantly, the persisted file's `generated_at` is the source of stability, not the in-memory one.

### Schema versioning

`schema_version: int = 1` is the first field on `Catalog`. `load_catalog` rejects files whose version doesn't match the code's expected version, falling back to a fresh build + overwrite. Future shape changes bump it and write a one-shot migration; today there's nothing to migrate.

## Stable `generated_at`

The mechanic:

1. `CatalogService.rescan()` walks sources, builds a new `Catalog` with `generated_at = datetime.now(UTC).isoformat(timespec="seconds")` and a freshly computed `content_hash`.
2. Loads the persisted `catalog.json` if present.
3. If the persisted `content_hash` equals the new one, **reuse the persisted catalog's `generated_at`**. The new in-memory `Catalog` is rebuilt with the old timestamp.
4. Otherwise, the new catalog wins. `save_catalog()` writes it to disk.

The catalog on disk is the durable source of "last known generated_at". A no-content-change rescan (a) writes nothing and (b) hands back a Catalog whose `generated_at` matches disk.

Across streamlit restarts: the persisted file is loaded, `content_hash` matches whatever's on disk, `generated_at` is the timestamp from when the world last actually changed. llama-swap's `config.yaml` was built from a catalog with the same hash at the same timestamp → `is_config_stale` is False.

## `content_hash` definition

A pure function over the discovered models, deterministic across runs and machines:

```python
def compute_content_hash(entries: list[ModelEntry]) -> str:
    norm = []
    for e in sorted(entries, key=lambda x: (x.source, x.name)):
        pieces = sorted(
            ((p.role, p.filename, p.bytes) for p in e.pieces),
            key=lambda t: (t[1], t[0], t[2]),
        )
        norm.append((e.source, e.name, e.total_bytes, pieces))
    blob = json.dumps(norm, sort_keys=False, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()
```

Excluded from the hash: `directory` (so a vault move doesn't reseat the timestamp — though a vault move is itself a config change), `notes`, `extra`. Including piece-level `role`/`filename`/`bytes` so additions or deletions of files flip the hash.

## `_is_llm_candidate` and the short-source label

Two callers in `genesis_worker/services/llama_swap/generate_config.py` use hardcoded source names today:

- Line 143: `if source == "huggingface":` — this is a real per-source filter (HF entries are checked for `.gguf` presence because HF holds non-GGUF repos too). It does **not** depend on the field-name legacy; it depends on the source plugin's identity. Keep as-is. A future source might add its own filter; that's a per-source concern, not a framework one.
- Line 208: `src_short = "hf" if source == "huggingface" else "lms"` — short label for entry IDs. Replace with a generic helper:

```python
def short_source_label(source: str) -> str:
    """Stable short label for entry IDs. Take consonants + first letters.
    'huggingface' -> 'hf'; 'lmstudio' -> 'lms'; 'comfyui' -> 'cm'.
    """
    if not source:
        return "x"
    # First letter of each underscore-delimited segment, up to 3 chars.
    parts = source.split("_")
    return "".join(p[0] for p in parts)[:3] or source[:3]
```

This rule is deterministic and survives adding new sources without per-source special cases.

## Catalog page auto-refresh

`genesis_worker/ui/catalog.py` gains a session-state tracker:

```python
last_seen_complete: set[str] = st.session_state.setdefault(
    "catalog_last_seen_complete", set()
)
current_sessions = worker.list_acquire_sessions()
newly_complete = {s["id"] for s in current_sessions if s["state"] == "complete"}
if newly_complete - last_seen_complete:
    catalog = worker.rescan_catalog()
    total = sum(len(v) for v in catalog.by_source().values())
    st.toast(f"Auto-refreshed after download — {total} models now")
last_seen_complete |= newly_complete
```

Two notes:

- The set is updated **after** the comparison, so a session that completes mid-render triggers exactly one toast.
- The check runs on every render, which is fine — `rescan_catalog` is fast when content hasn't changed (just the hash check) and Streamlit's reactive model makes this idempotent.

The manual "↻ Rescan catalog" button stays. The misleading comment that says rescan "writes to `~/.cache/genesis-worker/`" is removed — rescan now writes to `state_dir/catalog.json` only when content changes, and `~/.cache/` is never touched.

## Verification conditions

### Automated

- `uv run pytest -q` — all 230 existing tests pass + new tests for:
  - `Catalog.by_source()` returns `{source_name: [entries]}` after the refactor; works for zero, one, and many sources.
  - `compute_content_hash` is stable across calls with the same input; changes when any `(source, name, total_bytes, piece.role, piece.filename, piece.bytes)` tuple changes.
  - `save_catalog` skips the write when the serialized text is identical; writes when it differs; survives a concurrent write from a second worker (no truncation).
  - `load_catalog` rejects mismatched `schema_version` and rebuilds from scratch.
  - `CatalogService.rescan()` reuses the persisted `generated_at` when content is unchanged; advances it when content changes.
  - `GenesisWorker.catalog()` loads from disk on startup when present; rescan-and-saves when missing.
  - `short_source_label` returns deterministic short labels for sample sources.
- `uv run pyright` clean (standard mode).
- `uv run ruff check genesis_worker` clean.
- `genesis_worker/tests/test_plugin_boundary.py` still passes (the boundary didn't change; imports touched are framework-side).

### Manual

1. With an existing `config.yaml` from `make all`, start streamlit. Open the Catalog page. Verify the "Generated" timestamp matches what `config.yaml`'s `generated_at` says. No "stale" warning on the Config editor page.
2. Click "↻ Rescan catalog" — verify the file at `state_dir/catalog.json` is unchanged (no rewrite, `mtime` preserved).
3. Drop a fake model directory under the vault. Click "↻ Rescan catalog" — verify the file changes, `generated_at` advances, the new model appears in the list.
4. Restart streamlit. Open the Catalog page — verify the "Generated" timestamp matches what it was before the restart.
5. Run an HF acquire for a small repo. Wait for `complete`. Return to the Catalog page. Verify the new model appears in the list and a toast says "Auto-refreshed after download — N models now".

## Out of scope

- Catalog encryption or access control (it's a single-user worker on a single machine).
- Cross-vault catalog merging.
- Catalog history / rollback. (The on-disk file is always the latest known good state.)
- Migration of legacy `MODEL_CATALOG.yaml`. ADR-008's retirement phase covers that when `bin/catalog.py` is retired.

## Status

Accepted.