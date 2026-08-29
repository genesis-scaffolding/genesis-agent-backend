# ADR-014 Model Deletion

## Title

Full model deletion from catalog and disk

## Context

Users acquire models into the vault and register them in `catalog.json`. When disk space is tight or a model is no longer needed, there is no way to remove it — the catalog grows indefinitely and stale entries persist even if files are manually deleted. The framework owns the catalog; sources only walk and download — deletion is a framework operation.

## Decision

We will add a `delete_model(source, name)` method on `GenesisWorker` that:

1. Locates the matching entry in the in-memory catalog by `source` + `name`.
2. Recursively deletes the model directory from disk.
3. Removes the entry from the catalog.
4. Persists the updated catalog to `state_dir/catalog.json`.

If the directory does not exist (e.g. already manually deleted), step 2 is skipped and only the catalog entry is removed. If the entry is not found, `ValueError` is raised. Errors during directory deletion are propagated as-is.

The UI (`genesis_worker/ui/catalog.py`) gains a "Delete" button per model entry. Before deletion, a `st.dialog` confirmation is shown with the model name. On confirm, the facade method is called and the page reruns with a toast notification.

## Status

Proposed.

## Consequences

- Users can reclaim disk space from the UI without CLI access.
- The confirmation dialog prevents accidental deletions.
- Deletion is irreversible — there is no undo or trash.
- If the service (e.g. llama-swap) is using a model, deleting it while the service is running may cause runtime errors. The UI does not check service state; users are responsible for stopping services before deletion.
- `compute_content_hash` in `catalog_build.py` must be updated: the hash already excludes `directory`, so a rescan that finds the directory missing will produce the same hash as if the entry were deleted — no stale hash collision.


