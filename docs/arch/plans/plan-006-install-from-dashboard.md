# Plan 006: Install from dashboard

Implements [spec-006](../specs/spec-006-install-from-dashboard.md).

## Working rules

- Branch: `feature/service-install` (continuing from plan-005).
- No new dependencies.
- The running llama-swap on `:8080` is **not** stopped. The install-helper test exercises a scratch install root.
- `bin/`, `Makefile`, `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json` are **not** modified (ADR-008).
- One commit at the end. Wait for user verification before commit (AGENTS.md).
- Validation gates: `uv run pytest -q`, `uv run pyright`, `uv run ruff check genesis_worker`. Plus the boundary walker: `uv run pytest -q genesis_worker/tests/test_plugin_boundary.py`.

## Step 1: contract hook

- `genesis_worker/contracts/service.py` — add `primary_installable() -> ServiceInstall | None` to `InferenceService` with default `None` (the only `can_install`-bearing services override).

## Step 2: LlamaSwapService override

- `genesis_worker/services/llama_swap/service.py` — add:
  ```python
  def primary_installable(self) -> ServiceInstall | None:
      return self._llama_swap_install
  ```
  Place it next to `installs()`.

## Step 3: inline install helper

- `genesis_worker/utils/ui/_install_flow.py` — new file. One public function `render_inline_install(installable, *, key_prefix)` plus a private `_render_step(step)` that mirrors the rendering shape in `binaries.py` (success / error / progress bar / info fallback).
- The helper must not import from `binaries.py` — copy the 7-line `_render_step` so the helper is self-contained. Avoids shared-state surprises and keeps `binaries.py` from being a dependency for the dashboard's plumbing.
- The `drop_pending` flag pattern is exactly the one used in `binaries.py` — same shape, different `key_prefix`.

## Step 4: dashboard

- `genesis_worker/ui/dashboard.py`:
  - Import `from genesis_worker.utils.ui._install_flow import render_inline_install`.
  - In the per-service card body, replace the `if running / else` Start-Stop split with the three-way branch described in spec-006 §Dashboard.
  - Web UI link stays gated on `state == "running"`. Admin link stays.

## Step 5: status page

- `genesis_worker/services/llama_swap/ui/status.py`:
  - Import `render_inline_install`.
  - Replace the `if running / else` Start-Stop split in the top section with the three-way branch.
  - Binaries section is untouched.

## Step 6: tests

- `genesis_worker/tests/test_service_llama_swap.py`:
  - `test_primary_installable_returns_llama_swap_install` — `svc.primary_installable() is svc._llama_swap_install`. Easy; doesn't need a real install.
  - Augment `test_is_available_false_when_binary_missing` (or add a sibling) to assert `primary_installable()` is still non-`None` when unavailable.
- (Optional) `test_ui_helper_does_not_import_binaries` — AST-grep that `genesis_worker/utils/ui/_install_flow.py` does not import from `genesis_worker.services.llama_swap.ui.binaries`. Skip unless the duplication risk bites.

## Step 7: gates

All four must pass:

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run pytest -q genesis_worker/tests/test_plugin_boundary.py
```

## Step 8: manual verification

1. Confirm the framework is running and llama-swap is installed: `ls ~/.local/share/genesis-worker/llama-swap/installs/llama-swap/`.
2. `rm -rf ~/.local/share/genesis-worker/llama-swap/installs/llama-swap/` — uninstall via the framework's filesystem view.
3. Reload the dashboard. The llama-swap card should show **Install** (not **Start**).
4. Click **Install**. Progress bar appears, polls every ~2s.
5. On completion, the card re-renders with **Start** (and the install button is gone).
6. Click **Start**. Service starts.
7. Same exercise on the Status page: top section shows **Install** before step 4, then **Start** after.

## Step 9: commit

- Single commit. Message: `ui: install button on dashboard and status when service is unavailable (spec-006)`.
- Files per the changes in steps 1–6.
