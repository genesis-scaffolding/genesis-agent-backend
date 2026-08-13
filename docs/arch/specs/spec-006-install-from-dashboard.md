# Spec 006: Install from dashboard

## Goal

Refine ADR-012's surface so that the first-run onboarding flow is:

> uv → `streamlit run genesis_worker/ui/app.py` → click **Install** on the dashboard → wait → click **Start**.

Today, when `LlamaSwapService.is_available()` is False, the dashboard and Status page both render a **Start** button that fails silently with `"binary not installed"`. The user has to navigate to the Binaries page manually. We collapse that to one click.

Two pieces of work:

1. A new contract hook `InferenceService.primary_installable()` so the dashboard knows which installable to drive when the service is unavailable.
2. A small inline-install UI helper used by the dashboard and the llama-swap Status page.

The `is_available()` gate itself is already correct (returns `False` when the binary is missing). It stays.

## Contract change

`InferenceService` gains an optional method:

```python
def primary_installable(self) -> ServiceInstall | None:
    """The installable whose presence makes ``is_available()`` True, if any.

    The dashboard's one-click install button is driven by this. Default
    ``None`` — services with no install axis or where the install details
    are not yet modeled render their existing Start/Stop UI even when
    unavailable.
    """
    return None
```

`LlamaSwapService` overrides:

```python
def primary_installable(self) -> ServiceInstall | None:
    return self._llama_swap_install
```

The "primary" is the llama-swap binary, not the llama-server variants. The variants are still installed via the Binaries page (they need a backend pick — CUDA vs CPU vs Vulkan — which doesn't fit on the dashboard).

## Inline install helper

New file `genesis_worker/utils/ui/_install_flow.py`. One public function:

```python
def render_inline_install(installable: ServiceInstall, *, key_prefix: str) -> None: ...
```

`key_prefix` is the Streamlit widget key namespace (e.g. `"dash-llama_swap"`, `"status-llama_swap"`). The helper:

- If no session is in flight: render a single **Install** button. On click, `installable.install()` (no version → latest) and store the session in `st.session_state[f"{key_prefix}/session"]`. `st.rerun()`.
- If a session is in flight:
  - Render the current `AcquireStep` (success / error / progress bar / fetching…; the same `_render_step` shape used in `binaries.py`).
  - On terminal: drop the session on the next parent rerun via the `drop_pending` flag pattern (mirrors `binaries.py`). A **Dismiss** button drops immediately.
  - Mid-flight: wrap the step render in `@st.fragment(run_every="2s")` so the progress bar updates without a user click. On terminal, the fragment sets `drop_pending` and calls `st.rerun(scope="app")`.
  - A **Cancel** button calls `session.cancel()` and `st.rerun()`.

The helper does **not** show a version picker. The dashboard is the first-touch surface; version selection stays on the Binaries page (v1 keeps `selections.yaml` read-only).

## Dashboard

`genesis_worker/ui/dashboard.py`. The per-service card currently branches:

```python
if running:
    Stop
else:
    Start
```

becomes:

```python
if running:
    Stop
elif not svc.is_available() and caps.can_install:
    installable = svc.primary_installable()
    if installable is not None:
        render_inline_install(installable, key_prefix=f"dash-{info.name}")
    else:
        st.caption("Not installed")
else:
    Start
```

`Web UI` link stays gated on `running` (already correct). `Admin` link stays as-is.

## Status page

`genesis_worker/services/llama_swap/ui/status.py`. Same three-way branch in the top section. The Binaries section below is unchanged — it still shows every installable with the existing **Manage →** link, so when llama-swap is installed the user can install a llama-server variant from there.

## Tests

- `test_primary_installable_returns_llama_swap_install`: build `LlamaSwapService(service_ctx(tmp_path))`, assert `primary_installable() is srv._llama_swap_install` and not one of the llama-server variants.
- `test_is_available_false_when_binary_missing` (already exists, augmented to assert `primary_installable()` is still non-`None` when unavailable — the dashboard install button doesn't gate on the installable existing, only on availability).
- Default contract: `InferenceService.primary_installable()` returns `None` — covered by the abstract default; a unit test against a stub service can pin it.

UI is not unit-tested. Manual verification is in the verification block.

## Consequences

- Two new files: `docs/arch/specs/spec-006-install-from-dashboard.md`, `docs/arch/plans/plan-006-install-from-dashboard.md`, `genesis_worker/utils/ui/_install_flow.py`.
- Modified: `genesis_worker/contracts/service.py`, `genesis_worker/services/llama_swap/service.py`, `genesis_worker/ui/dashboard.py`, `genesis_worker/services/llama_swap/ui/status.py`, `genesis_worker/tests/test_service_llama_swap.py`.
- No changes to the install backend (the `LlamaSwapBinary` install flow is unchanged; this is purely the dashboard surface that drives it).

## Verification

- `uv run pytest -q` — all pass.
- `uv run pyright` — 0 errors.
- `uv run ruff check genesis_worker` — clean.
- `uv run pytest -q genesis_worker/tests/test_plugin_boundary.py` — 30 passing.
- Manual: with the framework running, `rm -rf <data>/llama-swap/installs/llama-swap/` (delete the llama-swap install). Reload the dashboard. The llama-swap card shows an **Install** button. Click; progress bar updates every ~2s. On completion, the card re-renders with **Start** (and the previous **Install** is gone). Click **Start**; service starts.
- Same exercise on the Status page: top section shows **Install**; click; progress; on completion the button becomes **Start**.

## Out of scope

- Other services (ComfyUI, vLLM) — they'll add their own `primary_installable()` overrides when their install backends land.
- Version picker on the dashboard — Binaries page is the version-pick surface.
- `bin/llama-swap` path-based install interaction — orthogonal; the framework's install location is the source of truth for `is_available()`.
