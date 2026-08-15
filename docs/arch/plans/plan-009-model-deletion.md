# Plan-009: Model Deletion

## Step 1 — Add `delete_model` to `GenesisWorker`

**File:** `genesis_worker/facade.py`

Add method `delete_model(self, source: str, name: str) -> None` to the `GenesisWorker` class.

Implementation:
1. Find the entry in `self._catalog_cache.entries` by matching `source` and `name`. Raise `ValueError` if not found.
2. Resolve `Path(entry.directory)`. If it exists, call `shutil.rmtree(directory)`.
3. Remove the entry from `self._catalog_cache.entries`.
4. Call `save_catalog(state_dir / "catalog.json", self._catalog_cache)`.

No new imports needed beyond `shutil` (already in stdlib).

---

## Step 2 — Add tests for `delete_model`

**File:** `genesis_worker/tests/test_facade.py` (append) or new test file

Cover:
- `delete_model` removes entry and directory.
- `delete_model` removes entry when directory already gone.
- `delete_model` raises `ValueError` for unknown `(source, name)`.
- Catalog file on disk is updated after deletion.

Use `tmp_path` fixtures for isolated catalog and vault directories.

---

## Step 3 — Add delete button + confirmation dialog to UI

**File:** `genesis_worker/ui/catalog.py`

1. Add the dialog function and session-state check before the section 1 container.
2. In the per-entry expander, add a "Delete" button keyed by `f"delete-{entry.source}-{entry.name}"`.
3. The button sets `st.session_state["delete_confirm"]` with `source` and `name`.
4. On confirm in the dialog, call `worker.delete_model(...)`, then `st.rerun()`.

---

## Step 4 — Verify

```bash
uv run pytest -q
uv run pyright genesis_worker/facade.py genesis_worker/ui/catalog.py
uv run ruff check genesis_worker/facade.py genesis_worker/ui/catalog.py
```

All must pass.
