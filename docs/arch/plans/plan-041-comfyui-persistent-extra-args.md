# Plan: ADR-041 — ComfyUI persistent extra-args

Implements ADR-041. Three phases, each ending with the test/type/lint
gate green.

## Phase 1 — Service setter + sidecar persistence

The plumbing for the persistence is already in
`genesis_worker/utils/config_overrides.py` (ADR-036). This phase adds
the ComfyUI-side write path and its tests.

### 1.1 `set_extra_args` on `ComfyUiService`

**`genesis_worker/services/comfyui/service.py`** — new method on the
service class. Place it next to `set_gpu_variant` since they're the
same shape (in-memory update + sidecar write):

```python
def set_extra_args(self, args: list[str]) -> None:
    """Update ``extra_args`` in memory and persist to the JSON sidecar.

    An empty list pops the ``extra_args`` key from the sidecar so the
    YAML default (``["--verbose"]``) re-emerges on the next worker
    restart. Non-empty lists are stored verbatim. The in-memory update
    means ``start()`` picks up the new value without a worker restart;
    the sidecar write means the value also survives a worker restart
    via ``Settings.options_for`` merging.
    """
    self._options.extra_args = list(args)
    from ...utils.config_overrides import (
        read_service_overrides,
        service_overrides_path,
        write_service_overrides,
    )
    sidecar = service_overrides_path(self._ctx.config_dir, self._ctx.name)
    overrides = read_service_overrides(sidecar)
    if args:
        overrides["extra_args"] = list(args)
    else:
        overrides.pop("extra_args", None)
    write_service_overrides(sidecar, overrides)
```

The `self._ctx` attribute is already set by `InferenceService.__init__`
(contracts/service.py:116) — no new wiring needed.

### 1.2 Tests

**`genesis_worker/tests/test_comfyui_service.py`** — three tests,
modelled on the existing `set_gpu_variant` tests:

- `test_set_extra_args_updates_memory` — `svc.set_extra_args(["--lowvram", "--gpu-only"])` then `svc._options.extra_args == ["--lowvram", "--gpu-only"]`.
- `test_set_extra_args_persists_to_sidecar_round_trip` — write, construct a fresh `ComfyUiService` from the same `tmp_path` / `config_dir` with no `extra_args` in options, assert the new instance sees the saved value (the sidecar merges into `ctx.options` via `Settings.options_for`, so the new construction has to use a `Settings` whose `services.comfyui` is empty — build one explicitly to keep the test independent).
- `test_set_extra_args_empty_clears_sidecar_key` — `set_extra_args(["--lowvram"])` then `set_extra_args([])` → sidecar file exists but contains no `extra_args` key.

Phase 1 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 2 — UI page

### 2.1 New `flags.py` page

**`genesis_worker/services/comfyui/ui/flags.py`** — multi-line text
area + Save + Save & restart. Mirror the existing UI conventions:

- `SERVICE_NAME = "comfyui"` constant (required by the import test).
- `worker = st.session_state["worker"]` + `svc = worker.service(SERVICE_NAME)` at the top.
- Use the same bordered-container layout as `image.py` / `models.py`.
- `st.text_area("Extra flags", value="\n".join(svc._options.extra_args), height=200)`.
- Parse: `[ln.strip() for ln in raw.splitlines() if ln.strip()]`.
- **Save** — `svc.set_extra_args(parsed)`; `st.success(f"Saved {len(parsed)} flag(s).")`; `st.rerun()`.
- **Save & restart** — same Save, then if `svc.is_running()` call `worker.stop_service(SERVICE_NAME)` then `worker.start_service(SERVICE_NAME)`. Surface `StartResult.message` on failure via the same start-error pattern as `_service_controls.py` (`st.session_state["flags-start_error"] = msg; st.rerun()`).
- Help caption: "Flags are appended after the bake-in `--models-directory /vault/comfyui` and the YAML default `--verbose`. Saved flags persist across worker restarts."

### 2.2 Register the page

**`genesis_worker/services/comfyui/service.py`** — `ui_pages` property
gets a fourth entry:

```python
UiPage(
    "Flags", ":material/flag:", ui_dir / "flags.py", url_path="comfyui_flags"
),
```

### 2.3 `__init__.py` docstring

**`genesis_worker/services/comfyui/ui/__init__.py`** — bump the
"Status, Image, Models" comment to "Status, Image, Models, Flags".

### 2.4 Status page: show the running args

**`genesis_worker/services/comfyui/ui/status.py`** — in the
`Container info` block, add a line that renders the resolved
extra_args. Read from `svc._options.extra_args` (already populated).
Show as `**Args:** \`--models-directory /vault/comfyui <user flags>\``
so the operator can see what's actually running. Read-only; the
editor lives on the Flags page.

### 2.5 UI import test

**`genesis_worker/tests/test_comfyui_ui_imports.py`** — add the
flags page to the `PAGES` list and add a `test_flags_page_parses`
that mirrors `test_image_page_parses`.

Phase 2 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 3 — End-to-end verification

Run the full gate from the repo root:

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

Smoke check the gate from a non-root cwd as well (per AGENTS.md "Tests
must pass from any working directory"):

```
cd /tmp && uv run --project /home/gentran/Documents/genesis-agent-backend pytest /home/gentran/Documents/genesis-agent-backend/genesis_worker/tests/test_comfyui_service.py -q
```

(If the project tooling needs a fixed cwd for resolution, document the
limitation; otherwise the gate must pass from `/tmp`.)

No docs tutorial update — the Flags page is self-documenting via
caption text, and the ADR captures the architectural pattern. A
declarative-services tutorial section for the new "Python plugin
bespoke page" precedent would be premature with one example.