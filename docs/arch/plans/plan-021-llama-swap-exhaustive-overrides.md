# Plan-021: Exhaustive override UI for llama-swap

## Step 1 — `ui/config_editor.py`: Remove rendering guards

**File:** `genesis_worker/services/llama_swap/ui/config_editor.py`

1. In `_render_override_form`, remove all `if cfg.field is not None or "field" in current_overrides:` guard blocks.
2. Ensure all fields defined in `EvaluatedConfig` are rendered regardless of their current value.
3. The `_render_effective` function must remain untouched.

## Step 2 — `ui/config_editor.py`: Implement "Unset" logic

**File:** `genesis_worker/services/llama_swap/ui/config_editor.py`

1. Update widget value resolution:
   * Use `current_overrides.get(field, cfg.field)` to pre-fill the widget.
2. Implement type-specific "unset" behavior to omit fields from `new_overrides`:
   * **Strings / Integers**: 
     * Use `st.text_input`.
     * If the value is an empty string (or whitespace), do not add the field to `new_overrides`.
     * For integers, cast the string to `int` only if non-empty.
   * **Booleans (`mmproj_offload`)**:
     * Add a companion checkbox `st.checkbox("Override mmproj_offload", ...)`.
     * Only if this "Override" box is checked, include the value of the actual toggle in `new_overrides`.
   * **JSON / Dicts (`sampling`, `spec`, `chat_template_kwargs`)**:
     * Use `st.text_area`.
     * If the resulting string is empty or only whitespace, do not add the field to `new_overrides`.

## Step 3 — Persistence

**File:** `genesis_worker/services/llama_swap/ui/config_editor.py`

1. Ensure the final `new_overrides` dictionary is passed to `svc.save_overrides_for_entry(entry_id, new_overrides)`.
2. Verify that fields omitted from `new_overrides` are effectively removed from `overrides.yaml`.

## Step 4 — Verify

```sh
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

Manual checks:
* Select a model with a sparse recipe.
* Verify all knobs (e.g., `mmproj_offload`, `reasoning_budget`) are now visible.
* Set a value $\rightarrow$ verify effective config updates.
* Unset the value (via empty string or unchecking "Override") $\rightarrow$ verify field is removed from `overrides.yaml` and reverts to recipe value.
