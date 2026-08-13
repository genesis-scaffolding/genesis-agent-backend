# Plan 004: Persistent catalog and source-agnostic shape

Implements [spec-004](../specs/spec-004-persistent-catalog-and-source-agnostic-shape.md) on top of [ADR-011](../adr-011-persistent-catalog-and-source-agnostic-shape.md).

## Working rules

- Branch: already on `feature/streamlit-refinements` off `main`. Continue here; this work is part of the same refinement series.
- `Makefile`, `bin/`, and repo-root state files are not modified (ADR-008).
- The running llama-swap on `:8080` is not stopped during validation.
- Verification gates after every chunk: `uv run pytest -q`, `uv run pyright`, `uv run ruff check genesis_worker`. All must pass from any cwd.
- Single-line commit messages, no body. One commit per chunk.
- Tests must pass from any working directory; anchor fixture paths with `repo_root()`.

## Chunks

### Chunk 1: `Catalog` shape — drop hardcoded source fields

1. `genesis_worker/contracts/catalog.py` — rewrite `Catalog`:
   - Remove `huggingface: list[ModelEntry]` and `lmstudio: list[ModelEntry]` fields.
   - Add `schema_version: int = 1`.
   - Add `content_hash: str`.
   - Add `entries: list[ModelEntry] = Field(default_factory=list)`.
   - Rewrite `by_source()` to group `entries` by `entry.source`.
2. `genesis_worker/catalog_build.py` — `_build_catalog` returns a `Catalog(entries=[...], content_hash=..., schema_version=1)`. No more `by_source = {"huggingface": [], "lmstudio": []}` initializer.
3. `genesis_worker/tests/test_catalog_build.py` — replace `cat.huggingface[0]` with `cat.by_source()["huggingface"][0]` and similar.
4. `genesis_worker/tests/test_config_emit.py` — same: replace `real_catalog.huggingface[0]` with `by_source()["huggingface"][0]`.
5. Run all checks. Commit.

### Chunk 2: `compute_content_hash` helper + tests

1. `genesis_worker/catalog_build.py` — add `compute_content_hash(entries: list[ModelEntry]) -> str` as a module-level function. Pure, deterministic, sha256-hex.
2. `_build_catalog` calls it after sorting entries.
3. `genesis_worker/tests/test_catalog_build.py` — add tests:
   - Same input → same hash.
   - Different piece bytes → different hash.
   - Different piece filename → different hash.
   - Empty entries → known-stable hash (sha256 of `"[]"`).
4. Run all checks. Commit.

### Chunk 3: Catalog I/O — load/save with no-op skip

1. New file: `genesis_worker/catalog_io.py`:
   - `load_catalog(path: Path) -> Catalog | None` — reads JSON, validates, returns `None` on any error (missing file, malformed JSON, schema mismatch).
   - `save_catalog(path: Path, catalog: Catalog) -> bool` — text-diff skip + atomic write via temp + `os.replace`. Returns whether a write happened.
2. `genesis_worker/tests/test_catalog_io.py` — new test file:
   - `save_catalog` writes on first call.
   - `save_catalog` skips when content identical (returns `False`, mtime unchanged).
   - `save_catalog` writes when content differs.
   - `load_catalog` round-trips a saved catalog.
   - `load_catalog` returns `None` on missing file, malformed JSON, schema-version mismatch.
   - Atomic write: simulate a reader holding a file handle across `save_catalog` and verify it never sees a half-written file.
3. Run all checks. Commit.

### Chunk 4: `CatalogService` — persistence + stable `generated_at`

1. `genesis_worker/catalog_build.py` — `CatalogService.__init__(self, registry: SourceRegistry, catalog_path: Path)`. The path is supplied by the facade, not the registry.
2. `CatalogService.rescan()`:
   - Walk sources, build new catalog with fresh `generated_at` and `content_hash`.
   - `previous = load_catalog(self._catalog_path)`.
   - If `previous is not None and previous.content_hash == new.content_hash`: return a new `Catalog` object with `previous.generated_at` (and same `root` if unchanged). Don't write.
   - Otherwise: `save_catalog(self._catalog_path, new)` and return `new`.
3. `genesis_worker/tests/test_catalog_build.py` — add tests:
   - First rescan on a fresh vault: writes, returns catalog with fresh `generated_at`.
   - Second rescan with no changes: no write, `generated_at` reused from disk.
   - Rescan after file added: writes, `generated_at` advances.
4. Run all checks. Commit.

### Chunk 5: `GenesisWorker` — wire catalog path

1. `genesis_worker/facade.py` — `GenesisWorker.__init__` constructs `CatalogService(self._source_registry, catalog_path=self._settings.paths.state_dir / "catalog.json")`.
2. `GenesisWorker.catalog()` — try `load_catalog` first; if it returns `None`, fall back to `self._catalog_service.rescan()`. This guarantees the worker sees a catalog on startup even if `rescan_catalog` was never explicitly called.
3. `GenesisWorker.rescan_catalog()` — delegate to `self._catalog_service.rescan()`.
4. `genesis_worker/tests/test_facade.py` — add a smoke test that constructs a worker pointed at a temp `state_dir`, calls `catalog()` twice, and verifies no second rescan writes to disk when content is unchanged.
5. Run all checks. Commit.

### Chunk 6: Short-source label helper

1. `genesis_worker/services/llama_swap/generate_config.py` — add `short_source_label(source: str) -> str` (the rule from the spec).
2. Replace line 208's `src_short = "hf" if source == "huggingface" else "lms"` with `src_short = short_source_label(source)`.
3. `_is_llm_candidate` (line 143) keeps its `if source == "huggingface":` check unchanged.
4. `genesis_worker/tests/test_generate_config.py` — extend the entry-ID test to verify the helper produces the expected labels for `"huggingface"`, `"lmstudio"`, and a hypothetical third source.
5. Run all checks. Commit.

### Chunk 7: Catalog page auto-refresh + comment fix

1. `genesis_worker/ui/catalog.py`:
   - At the top of the file, after `worker = st.session_state["worker"]`, add the auto-refresh block from the spec.
   - Remove the misleading comment at the original line 43 ("Rescan is a destructive-feeling action (writes to ~/.cache/genesis-worker/)"). Rescan writes to `state_dir/catalog.json` only when content changes.
   - Keep the manual "↻ Rescan catalog" button.
2. Manual smoke test: run an HF acquire for a tiny repo, watch the Catalog page, verify the new model appears without manual click. (This one is manual by nature; the existing UI test infrastructure is page-snapshot based and the auto-refresh relies on session state.)
3. Run all checks. Commit.

### Chunk 8: Catalog UI cleanup for the new shape

1. `genesis_worker/ui/catalog.py` — the existing `tab_labels = [s.display_name for s in sources]` and per-source tab rendering already use `by_source().get(source.name, [])`. No structural change needed; verify nothing references removed fields.
2. `genesis_worker/ui/dashboard.py` — the catalog debug panel uses `catalog.by_source().get(info.name, [])`. Same: verify and confirm.
3. `genesis_worker/cli/catalog.py` — uses `catalog.by_source()`. Verify and confirm.
4. If anything slips through, fix it. Run all checks. Commit.

### Chunk 9: ADR link-back and cleanup

1. `docs/arch/adr-008-migration-strategy.md` — annotate the legacy "Catalog shape" footnote as "Retired by [ADR-011](adr-011-persistent-catalog-and-source-agnostic-shape.md)." Same one-line pattern used elsewhere in ADR-008 for the ADR-009 supersession.
2. Verify all ADRs still read coherently. Commit docs separately from code.

## Post-implementation verification

Run the spec's verification conditions end to end:

1. `uv run pytest -q` — 230 existing + new tests, all pass.
2. `uv run pyright` — 0 errors.
3. `uv run ruff check genesis_worker` — 0 issues.
4. Manual streamlit run against the live vault:
   - Verify "Generated" timestamp on the Catalog page equals `config.yaml`'s `generated_at`.
   - Verify "↻ Rescan catalog" is a true no-op on an unchanged vault (`stat -c %Y catalog.json` unchanged across clicks).
   - Verify auto-refresh works after an HF acquire completes.
   - Verify the file persists across `Ctrl-C` and `uv run genesis-worker-ui` again.

## Out of scope (deferred)

- Retirement of `bin/catalog.py` and migration of `MODEL_CATALOG.yaml` (ADR-008 Phase 10).
- Catalog encryption / access control.
- Cross-vault catalog merging.