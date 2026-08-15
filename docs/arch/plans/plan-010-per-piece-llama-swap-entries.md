# Plan-010: Per-piece llama-swap config entries

Steps to implement [spec-010](../specs/spec-010-per-piece-llama-swap-entries.md).

## Step 1 — Rename `DetectedFiles` → `DetectedFileSet`

**File:** `genesis_worker/services/llama_swap/generate_config.py`

Rename the dataclass `DetectedFiles` to `DetectedFileSet`. Update all references in the file. Add a `piece_bytes: int` field (alias for the old `weight_bytes`; keep `weight_bytes` as a rename for now to minimize diff, then deprecate it in a follow-up).

## Step 2 — Replace `detect_files` with `detect_file_sets`

**File:** `genesis_worker/services/llama_swap/generate_config.py`

Replace the function:

```python
# OLD
def detect_files(entry: ModelEntry) -> DetectedFileSet:
    mains = [p for p in entry.pieces if p.role == "main"]
    main = max(mains, key=lambda p: p.bytes).path if mains else None
    ...
    return DetectedFileSet(main=main, mmproj=..., draft=..., is_mtp=..., weight_bytes=entry.total_bytes)

# NEW
def detect_file_sets(entry: ModelEntry) -> list[DetectedFileSet]:
    mains = [p for p in entry.pieces if p.role == "main"]
    mmprojs = [p for p in entry.pieces if p.role == "mmproj"]
    drafts = [p for p in entry.pieces if p.role == "mtp"]
    is_mtp = bool(drafts) or "mtp" in entry.name.lower() or any("mtp" in p.filename.lower() for p in mains)
    base = DetectedFileSet(
        mmproj=mmprojs[0].path if mmprojs else None,
        draft=drafts[0].path if drafts else None,
        is_mtp=is_mtp,
        piece_bytes=0,  # set below
    )
    return [
        DetectedFileSet(
            main=p.path,
            mmproj=base.mmproj,
            draft=base.draft,
            is_mtp=base.is_mtp,
            piece_bytes=p.bytes,
        )
        for p in mains
    ]
```

Note: `is_mtp` is still derived from `entry.name` and all `mains` filenames — unchanged.

## Step 3 — Update `walk_models`

**File:** `genesis_worker/services/llama_swap/generate_config.py`

In `walk_models`, after `files = detect_files(entry)`, replace with the loop:

```python
for file_set in detect_file_sets(entry):
    for recipe in resolved.matched:
        entry_id = make_entry_id(
            file_set.main.name,  # piece filename
            recipe,
            multi_match=multi,
            all_ids=all_ids,
            source=source_key,
        )
        yield ModelMatch(
            entry_id=entry_id,
            entry=entry,
            source=source_key,
            recipe=recipe,
            multi_match=multi,
            files=file_set,
            entry_overrides=ovr.get(entry_id),
        )
```

## Step 4 — Update `build_entry`

**File:** `genesis_worker/services/llama_swap/generate_config.py`

In `build_entry`, remove the `files = detect_files(entry)` call (already passed in from `walk_models`/`build_config`). Keep the rest unchanged.

## Step 5 — Update `__all__` and exports

**File:** `genesis_worker/services/llama_swap/generate_config.py`

- Remove `detect_files` from `__all__`
- Add `detect_file_sets` to `__all__`

Also update the re-export in `service.py` if needed.

## Step 6 — Update tests

**File:** `genesis_worker/tests/test_generate_config.py` (or wherever config generation is tested)

- Rename `DetectedFiles` fixtures to `DetectedFileSet`.
- Update `detect_files` test cases to `detect_file_sets`; assert the return is a list and has the expected number of elements per quant scenario.
- Update entry-ID assertions to expect quant suffixes.
- Update config-entry-count assertions: a multi-GGUF entry should produce N×M entries (N GGUF files × M matched recipes).
- Ensure single-GGUF test cases still produce exactly 1 entry.
