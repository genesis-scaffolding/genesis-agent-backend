# Spec-009: Model Deletion

## Overview

Add `delete_model(source, name)` to `GenesisWorker` for full catalog + disk deletion, surfaced via a confirmation-dialog button in the catalog UI page.

---

## 1. Facade: `GenesisWorker.delete_model`

**File:** `genesis_worker/facade.py`

```python
def delete_model(self, source: str, name: str) -> None:
    """Delete ``name`` from ``source``: removes entry from catalog and wipes the directory.

    Raises:
        ValueError: no entry matching (source, name) exists.
    """
```

**Behaviour:**

1. Find `entry = next(e for e in self.catalog().entries if e.source == source and e.name == name)`. Raise `ValueError` if not found.
2. `directory = Path(entry.directory)`.
3. If `directory.exists()`: `shutil.rmtree(directory)`.
4. Remove `entry` from `self._catalog_cache.entries`.
5. `save_catalog(self._settings.paths.state_dir / "catalog.json", self._catalog_cache)`.

**Notes:**

- `self._catalog_cache` is mutated in-place; the persisted file is updated.
- `shutil.rmtree` is used directly; no custom wrapper needed.
- No locking — the service should be stopped before deletion.

---

## 2. UI: Delete button + confirmation dialog

**File:** `genesis_worker/ui/catalog.py`

Changes in the per-entry expander loop:

```python
for entry in entries:
    label = f"{entry.name}  ({format_bytes(entry.total_bytes)})"
    with st.expander(label):
        render_entry(entry)
        if st.button("Delete", key=f"delete-{entry.source}-{entry.name}"):
            st.session_state["delete_confirm"] = {
                "source": entry.source,
                "name": entry.name,
            }
```

**Confirmation dialog** (placed before the section 1 container):

```python
if "delete_confirm" in st.session_state:
    target = st.session_state["delete_confirm"]
    st.session_state["delete_confirm"] = None  # consume immediately

    @st.dialog("Delete model?")
    def confirm():
        st.write(f"**{target["name"]}** and all its files will be permanently deleted.")
        col1, col2 = st.columns(2)
        if col1.button("Delete", type="primary"):
            worker.delete_model(target["source"], target["name"])
            st.rerun()
        if col2.button("Cancel"):
            st.rerun()

    confirm()
```

On successful deletion, the dialog callback completes, `st.rerun()` re-renders the page fresh with the model gone. A toast can be added after the rerun by storing a flag in session state before the rerun.

---

## 3. Verification Conditions

- `GenesisWorker().delete_model("huggingface", "org/repo")` removes the entry from `catalog.json` and deletes the directory.
- Calling with a non-existent `(source, name)` raises `ValueError`.
- Deleting a model whose directory is already gone removes only the catalog entry.
- The UI shows a confirmation dialog with the model name before any deletion.
- After confirming, the model no longer appears in the catalog listing.
- `uv run pytest -q`, `uv run pyright`, `uv run ruff check genesis_worker` all pass.
