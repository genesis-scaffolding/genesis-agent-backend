# ADR-041: ComfyUI persistent extra-args — Python plugin option persistence via JSON sidecar

## Title

ComfyUI persistent extra-args — a Python (non-declarative) plugin exposes a setter that updates in-memory state and writes to the per-service JSON sidecar, with a bespoke Streamlit page to drive it.

## Status

Accepted.

## Context

`ComfyUiOptions.extra_args` (`list[str]`, default `["--verbose"]`) controls the CLI flags the ComfyUI container runs with, alongside the hardcoded `--models-directory /vault/comfyui` prefix set in `service.py:275`. Operators want to test VRAM and cache flags (`--lowvram`, `--gpu-only`, `--highvram`, `--cache-classic`, `--cache-lru`, `--disable-metadata`, etc.) without rebuilding the container image — today the only way to change `extra_args` is to edit `user-overrides.env` with the `GENESIS_SERVICES__COMFYUI__EXTRA_ARGS=--lowvram,--gpu-only` shape, which is a yaml edit, not a UI affordance.

The persistence plumbing already exists. ADR-036 introduced `<config_dir>/services/<name>.overrides.json` — a per-service JSON sidecar merged into `ctx.options` at construction time by `Settings.options_for("services", name)`. That merge is generic; it doesn't care whether the service is declarative or Python. So writes to the sidecar **already survive a worker restart** for ComfyUI today; what's missing is the write path and the UI to drive it.

### What's new for Python plugins

The declarative `configure` panel in `genesis_worker/utils/services/panels.py` is built for YAML services — it reads `svc.config.option_specs` (only declarative services expose that attribute) and auto-generates a form per `OptionSpec`. Python plugins can't use it. Today, no Python plugin lets the user edit a single option from the UI; the only examples of "write to the sidecar from a UI" are declarative.

This ADR establishes the precedent for Python plugins: a Python plugin exposes an explicit `set_<option>(value)` method that updates the in-memory `_options` field **and** writes to the JSON sidecar, with a bespoke Streamlit page that calls the setter. The set / persist split is two lines and the sidecar helper (`write_service_overrides`) is already there.

### Why a bespoke page, not the declarative `configure` panel

- `configure` reads `option_specs` (declarative only); there's no way to graft ComfyUI's `extra_args` onto it without changing the panel's data model.
- A free-form text area (one flag per line) is the right UX for "any flag ComfyUI takes" — the curated set is long and version-dependent.
- The page only owns one field; generalising to all ComfyUI options would duplicate the panel system for no gain.

## Decision

### 1. `ComfyUiService.set_extra_args(args: list[str])`

A new method on the service. It does two things in order:

1. Updates the in-memory field: `self._options.extra_args = list(args)`.
2. Persists to the sidecar: reads the current sidecar at `service_overrides_path(self._ctx.config_dir, self._ctx.name)`, sets `overrides["extra_args"] = list(args)` (or pops the key when `args` is empty so the YAML default re-emerges), writes atomically via `write_service_overrides`.

Both happen together. The in-memory update means "Save & restart" can update flags on a running service without a worker restart; the sidecar write means a worker restart later picks up the same value via `Settings.options_for`.

The method accepts `list[str]` directly — the UI parses the text area into a list before calling. Empty list means "use the YAML default (`["--verbose"]`)" — passing `[]` is the documented revert path.

### 2. New `Flags` UI page

`genesis_worker/services/comfyui/ui/flags.py` — registered in `service.py`'s `ui_pages` list with label "Flags" and URL path `comfyui_flags` (mirroring the explicit-URL convention used by `comfyui_status` / `comfyui_image` / `comfyui_models`).

The page renders:

- A multi-line `st.text_area` with one flag per line. Pre-populated from `svc._options.extra_args`.
- A short help caption noting that `--models-directory /vault/comfyui` is hardcoded before the user's flags and is effectively a no-op to override (ComfyUI argparse takes last-wins for duplicate flags, so a user-supplied `--models-directory <other>` appended to the list will override it).
- **Save** — parses the text area into a list and calls `svc.set_extra_args(...)`. No restart.
- **Save & restart** — Save, then if the container is running, call `worker.stop_service("comfyui")` followed by `worker.start_service("comfyui")`. If the start fails, the saved config stays persisted and the container stays stopped (matches ADR-036's "Apply & restart" semantics — no rollback).

The page lives at `ui/flags.py` rather than being a section of the Status page because:

- The Status page is already busy (status, controls, container info, GPU variant, console).
- A dedicated page matches the existing `image` / `models` split.

### 4. Test on the UI

`genesis_worker/tests/test_comfyui_ui_imports.py` adds `flags.py` to the parsed pages list. The pattern (one `test_*_page_parses` per page) is identical to the existing pages.

### 5. Tests on the setter

`genesis_worker/tests/test_comfyui_service.py` gets three new tests:

- `set_extra_args` updates `_options.extra_args` in memory.
- `set_extra_args` round-trips through the sidecar (write → fresh service constructed from same `config_dir` → sees the new value).
- `set_extra_args([])` removes the key from the sidecar so the YAML default re-emerges on the next construction.

The existing test `test_default_extra_args_mirrors_compose` stays as-is; it tests the YAML default, which the sidecar absence now restores.

### 6. Status page unchanged

The Status page keeps its existing layout. The current flags show up in the Container info block as `**Args:** \`--models-directory /vault/comfyui --verbose\`` (new line) so operators can see what the running container was launched with. This is read-only; the editor is on the Flags page.

## Consequences

**Positive**

- Operators can iterate on VRAM/cache flags from the UI without touching yaml or rebuilding the container.
- The persistence path is reusable — any future Python plugin can add its own `set_<option>()` method + bespoke page using the same primitives (`write_service_overrides`, `service_overrides_path`).
- The "Save & restart" path mirrors ADR-036's semantics: persist wins on restart failure, no rollback.

**Negative**

- A bespoke page per field per Python plugin doesn't scale. If a future Python plugin needs to expose five options, it ends up with five pages. The middle ground (a Python-plugin-aware `configure` panel that reads from the plugin's options class metadata) is real but out of scope here — the field count is the trigger for that conversation.
- The text-area form offers no validation beyond "non-empty lines". A typo (`--lowvramn`) fails at container start with a ComfyUI-side error, surfaced through the existing start-error streamlit pattern. Acceptable for a testing affordance.

**Neutral**

- The default `["--verbose"]` stays in code; the sidecar's absence restores it. Tests for the default continue to pass without change.
- The JSON sidecar gains an `extra_args` key for ComfyUI when the user has saved flags. The file already exists for declarative services; for ComfyUI it appears on first save.

## Plan

`docs/arch/plans/plan-041-comfyui-persistent-extra-args.md` — file-by-file execution in three phases.

1. ADR (this file).
2. Service setter + sidecar write path + tests.
3. UI page + ui-page import test.