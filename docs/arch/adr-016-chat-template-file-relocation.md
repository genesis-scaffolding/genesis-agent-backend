# ADR-016 — Chat Template File Relocation

## Title

Bundle chat template files inside the pip package

## Context

The `gemma4` recipe in `recipes.yaml` references a chat template file via an
absolute path hardcoded to the developer's checkout:

```yaml
chat_template_file: "/home/gentran1991/Documents/GitHub/my-agent-backend/templates/gemma-4-chat-template.jinja"
```

This worked when `recipes.yaml` lived at the repo root and everything resolved
from there. After ADR-011 the package ships `recipes.yaml` inside the pip
distribution at `genesis_worker/services/llama_swap/data/recipes.yaml`. The
template file still lives at the repo root `templates/`, which means the path
is invalid on every other machine and every other install location.

## Decision

1. **Move** `templates/gemma-4-chat-template.jinja` → `genesis_worker/services/llama_swap/data/`.

2. **Update** `recipes.yaml` to use the bare filename:
   ```yaml
   chat_template_file: gemma-4-chat-template.jinja
   ```

3. **Update** `cmd_from_evaluated_dict` to resolve `chat_template_file`: if it
   is a relative path, resolve it against `BUNDLED_RECIPES_PATH.parent` (the
   `llama_swap/` package directory). Absolute paths are passed through as-is
   (for user overrides that specify a full path).

The template is "frozen at install time", matching the behaviour of
`recipes.yaml` itself. Users who need to customise it can override the field
via `overrides.yaml` or the Config Editor UI.

## Status

Accepted

## Consequences

- **Positive**: `chat_template_file` works identically in development and after
  `pip install`. No environment variables, no assumptions about where the
  checkout lives.
- **Positive**: The template file and the recipe that references it live
  together in the package.
- **Negative**: Editing the template in the repo requires `pip install -e .`
  (or a reinstall) to take effect in a non-editable install. This is also
  true of `recipes.yaml` and is an accepted limitation of the shipping
  model.
- **Neutral**: `cmd_from_evaluated_dict` gains a small path-resolution branch.
  No new public interface is added.


