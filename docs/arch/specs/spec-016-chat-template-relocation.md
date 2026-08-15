# Spec-016 — Chat Template File Relocation

## Overview

Move the gemma-4 chat template into the pip package and update
`cmd_from_evaluated_dict` to resolve relative `chat_template_file` paths from
`BUNDLED_RECIPES_PATH.parent`.

## Changes

### 1. File move

```
templates/gemma-4-chat-template.jinja
  → genesis_worker/services/llama_swap/data/gemma-4-chat-template.jinja
```

`templates/` at the repo root is removed if it contains no other files
after the move.

### 2. `recipes.yaml` — `gemma4` recipe

```yaml
# Before
chat_template_file: "/home/gentran1991/Documents/GitHub/my-agent-backend/templates/gemma-4-chat-template.jinja"

# After
chat_template_file: gemma-4-chat-template.jinja
```

All other recipes that reference `chat_template_file` follow the same pattern
(bare filename if the file lives in the package data dir; absolute path if
user-provided via override).

### 3. `generate_config.py` — `cmd_from_evaluated_dict`

Add a helper that resolves `chat_template_file` before emitting the CLI flag.

```python
def _resolve_chat_template_file(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str((BUNDLED_RECIPES_PATH.parent / p).resolve())
```

In `cmd_from_evaluated_dict`, replace:
```python
if chat_template_file:
    sections.append(f"  --chat-template-file {chat_template_file} \\")
```
with:
```python
if chat_template_file:
    resolved = _resolve_chat_template_file(chat_template_file)
    sections.append(f"  --chat-template-file {resolved} \\")
```

The `_resolve_chat_template_file` function is **private** — it is not added to
`__all__` or any public interface.

### 4. Tests

`test_chat_template_file_resolution` (in `tests/`):
- Absolute path passes through unchanged.
- Relative path resolves relative to `BUNDLED_RECIPES_PATH.parent`.
- Non-existent resolved path does not raise (the caller may be testing;
  llama-server will surface the error at runtime).

## Verification Conditions

1. `uv run pytest -q` passes.
2. `uv run pyright` passes.
3. `uv run ruff check genesis_worker` passes.
4. For a gemma4 model, `cmd_from_evaluated_dict` produces a
   `--chat-template-file` flag whose value points inside the pip package
   install directory (not the repo root).
5. An absolute-path override (e.g. `/tmp/my-template.jinja`) in
   `overrides.yaml` is passed through unchanged.
