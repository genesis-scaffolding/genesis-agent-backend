# Plan-016 — Chat Template File Relocation

## Steps

### 1. Move the template file

```bash
mv templates/gemma-4-chat-template.jinja \
   genesis_worker/services/llama_swap/data/
rmdir templates 2>/dev/null || true   # remove dir if now empty
```

### 2. Update `recipes.yaml`

In the `gemma4` recipe, replace the absolute path with the bare filename:
```yaml
- chat_template_file: "/home/gentran1991/Documents/GitHub/my-agent-backend/templates/gemma-4-chat-template.jinja"
+ chat_template_file: gemma-4-chat-template.jinja
```

### 3. Update `generate_config.py`

- Import `BUNDLED_RECIPES_PATH` from `.recipes`.
- Add `_resolve_chat_template_file` helper (private function, module-level).
- In `cmd_from_evaluated_dict`, replace the raw `chat_template_file`
  interpolation with `resolved = _resolve_chat_template_file(chat_template_file)`.

### 4. Add tests

New test function in `tests/` (or existing test file for `generate_config`):
`test_chat_template_file_resolution`.

### 5. Verify

```bash
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

## File diff (surface only)

```
genesis_worker/services/llama_swap/data/
  + gemma-4-chat-template.jinja   (moved from templates/)

genesis_worker/services/llama_swap/data/recipes.yaml
  ~ chat_template_file value in gemma4 recipe

genesis_worker/services/llama_swap/generate_config.py
  + import BUNDLED_RECIPES_PATH
  + _resolve_chat_template_file()
  ~ cmd_from_evaluated_dict body

templates/
  - gemma-4-chat-template.jinja   (moved out)

tests/ (new or existing)
  + test_chat_template_file_resolution
```
