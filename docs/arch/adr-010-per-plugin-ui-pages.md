# ADR-010: Per-plugin UI pages for the Streamlit dashboard

## Title
Per-plugin UI pages for the Streamlit dashboard

## Context

The worker needs a Streamlit-based management UI reachable from Tailscale for managing local AI infrastructure. The current spec (spec-003) commits to:

- One Streamlit entry point (`streamlit_app/app.py`) with auto-discovered pages in `streamlit_app/pages/`.
- A fixed page set — dashboard, catalog, acquire, config editor, recipes view, pi export — all in one location.
- A single dashboard listing every service side-by-side.

Two problems emerged.

**1. The framework/plugin boundary leaks into the UI layer.** Adding a new service (ComfyUI, AIToolkit, vLLM) requires new files in `streamlit_app/pages/` and edits to the dashboard's per-service rendering. ADR-009 ("adding an extension must not require editing framework code") is silently violated by the UI design. The boundary is solid in the Python layer and porous in the UI layer.

**2. The dashboard is an information surface, not a control surface.** The actual workflow on a phone is *swap which service is running to manage VRAM* — stop llama-swap, start comfyui, check system VRAM, repeat. Today's spec makes the user navigate into each service to act on it; the dashboard can't.

There's also a naming collision waiting to happen: `ServiceCapabilities.has_web_ui` describes whether a service has its *own* web interface on its native port (llama-swap at `:8080`, comfyui at `:8188`). Whether a plugin ships *worker-managed* Streamlit pages is a different question. We need to make that distinction explicit before both meanings get confused.

## Decision

### Plugin-owned UI pages

Each plugin declares the Streamlit pages it contributes. The framework never reaches behind the plugin's facade — it calls a single property on the existing ABC.

```python
# contracts/ui.py  (new — 8 lines)
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class UiPage:
    label: str          # sidebar text
    icon: str           # streamlit icon identifier (e.g. ":material/tune:")
    path: Path          # absolute path to a .py file Streamlit can execute
```

```python
# contracts/service.py — added to InferenceService ABC
@property
@abstractmethod
def ui_pages(self) -> list[UiPage]: ...

# contracts/source.py — added to ModelSource ABC
@property
@abstractmethod
def ui_pages(self) -> list[UiPage]: ...
```

Each page is a real `.py` file under the plugin's directory that Streamlit can execute. Streamlit's reactive model is preserved — top-level script execution on each rerun — and plugin authors can ship helper modules alongside.

### Directory layout

```
genesis_worker/
  ui/
    dashboard.py                       ← framework-owned (cross-service control surface)
    catalog.py                         ← framework-owned (browse the vault at length)
  services/
    llama_swap/
      ui/
        status.py                      ← landing page (always present)
        config_editor.py               ← only when the service has config to override
        recipes_view.py
        pi_export.py
  sources/
    huggingface/
      ui/
        acquire.py                     ← landing page (always present)
        session_list.py                ← optional: in-flight acquire sessions

streamlit_app/
  app.py                               ← thin shell: worker + page discovery + nav
  run.sh                               ← shell wrapper (binds 0.0.0.0)
```

`streamlit_app/pages/` is gone. The framework uses `st.navigation` programmatically rather than the `pages/` directory convention.

### Page discovery and grouping

```python
# streamlit_app/app.py — sketch, ~40 lines
from genesis_worker import GenesisWorker

@st.cache_resource
def get_worker() -> GenesisWorker:
    return GenesisWorker()

worker = get_worker()
st.session_state["worker"] = worker

nav = {"Overview": [
    st.Page(FRAMEWORK_UI / "dashboard.py", title="Dashboard", icon=":material/dashboard:"),
    st.Page(FRAMEWORK_UI / "catalog.py",   title="Catalog",   icon=":material/folder:"),
]}
for svc in worker.services.all():
    nav[svc.display_name] = [
        st.Page(p.path, title=p.label, icon=p.icon) for p in svc.ui_pages
    ]
for src in worker.sources.all():
    nav[src.display_name] = [
        st.Page(p.path, title=p.label, icon=p.icon) for p in src.ui_pages
    ]

st.navigation(nav).run()
```

The sidebar groups come from `plugin.display_name`. Streamlit renders them as sectioned lists — no custom component, no CSS, the canonical API.

### Primary (landing) page convention

The first entry in a plugin's `ui_pages` is its landing page. The framework reads this convention; plugins are responsible for ordering. The rule is documented in AGENTS.md ("How to write a plugin") and enforced by code review. If we later need an explicit override, we add `is_primary: bool = False` to `UiPage` then — not preemptively.

### The dashboard as a control surface

The framework dashboard (`genesis_worker/ui/dashboard.py`) is the cross-service control surface. It renders:

- **System strip:** live VRAM used / total, CPU, RAM. Refreshes on a slow interval (10s in v1).
- **Services:** one tile per service, in this shape:

```
┌──────────────────────────────────┐
│ llama-swap        ● RUNNING      │
│ Inference service                │
│ ~12 GB VRAM                      │
│                                  │
│ [ Stop ]   [ Admin ]   [ Web UI ↗] │
└──────────────────────────────────┘
```

  - `[ Stop ]` / `[ Start ]` — always present, paired with the state indicator. Renders inline; no navigation.
  - `[ Admin ]` — always present. `st.switch_page()` to the service's landing page.
  - `[ Web UI ↗ ]` — only when `has_web_ui` AND the service is running. `st.link_button()` to `status.endpoint`.

- **Vault section:** tabbed catalog (one tab per source) + an Acquire widget (select source → navigate to its acquire landing page).

The dashboard consumes only contract methods already on the ABC: `status()`, `stop()`, `start()`, `resource_estimate()`, `config_path()`. No new contract methods are needed for the tile to work.

### State model

| What | Where |
|---|---|
| `GenesisWorker` instance | `@st.cache_resource` in `streamlit_app/app.py` |
| Worker reference on every page | `st.session_state["worker"]`, set once by `app.py` |
| Acquire sessions | `worker._acquire_sessions` (on the cached instance) |
| Per-plugin UI state (overrides drafts, etc.) | Plugin's own store on the service instance |
| Per-page UI ephemera | `st.session_state` |

Plugins do not import `genesis_worker.facade.GenesisWorker`; they read the cached instance from `st.session_state["worker"]`. ADR-009 still holds — the page is plugin code, but the runtime injection is via session state, not a fresh instantiation.

### `has_web_ui` vs. management UI

`ServiceCapabilities.has_web_ui` keeps its existing meaning: the service exposes its own web UI on its native port (`llama-swap` at `:8080`, future `comfyui` at `:8188`). Whether the service ships worker-managed Streamlit pages is independent and is implied by `len(svc.ui_pages) > 0`. The distinction is documented inline at `ServiceCapabilities.has_web_ui` (a one-line comment) and elaborated in this ADR's Spec link. No new capability flag is added.

### Polling and refresh

v1 uses `time.sleep + st.rerun()` for progress indication (matches spec-003 today). The dashboard's slow auto-refresh is implemented as the same pattern in a loop guarded by a fragment boundary. The upgrade path to `st.fragment(run_every=...)` is noted in plan-003 as a v2 improvement, not paid for now.

## Status
Proposed.

## Consequences

**Positive**

- Adding a service or source is purely additive: drop a subpackage under `services/` or `sources/`, declare `ui_pages`, ship `.py` files. No edit to `streamlit_app/`, no edit to `genesis_worker/registries.py`, no edit to the framework. ADR-009 holds end to end.
- The dashboard becomes a usable control surface for the actual workflow (VRAM-swap on a phone). Stop/start, lifecycle status, VRAM estimate and live system VRAM are all visible from one screen.
- Plugin authors control their own UX. HF's acquire wizard can differ from a future ModelScope wizard without framework changes.
- `st.navigation` is the canonical Streamlit mechanism. No custom routing, no CSS, no hacks.
- The `has_web_ui` / management-UI distinction is now explicit, preventing the naming collision.

**Negative**

- Plugin pages must conform to Streamlit's reactive script model (top-level execution each rerun, `st.session_state` for persistence). Plugin authors need some Streamlit familiarity. Mitigated by an "How to write a plugin page" section in AGENTS.md.
- Pages are `.py` files, not callables. Harder to unit-test in isolation than a function. Mitigated by integration smoke tests that boot the Streamlit server and hit specific URLs.
- Convention-based primary page is fragile under list reordering. Mitigated by code review and explicit documentation. Cost paid only when the convention breaks — `is_primary` can be added later without an ADR.
- The framework directory gains `genesis_worker/ui/` and `genesis_worker/contracts/ui.py`. Two new locations, both small and well-scoped.
- Each service's UI depends on the framework for the dashboard. The framework depends on each service's contract methods. This is bidirectional at the dashboard layer — but the framework only ever touches contract methods, so ADR-009 still holds.

**Neutral**

- `streamlit_app/pages/` is no longer used.
- `contracts/ui.py` is a new module containing only `UiPage`. Single-purpose, no logic.
- The `streamlit_app/` directory shrinks to two files (`app.py`, `run.sh`).
- Polling cadence is a UX concern, not architectural. Settled in spec-003 / plan-003.

## Spec
[docs/arch/specs/spec-003-facade-and-ui.md](../specs/spec-003-facade-and-ui.md) — to be revised against this ADR.

## Plan
[docs/arch/plans/plan-003-facade-and-ui.md](../plans/plan-003-facade-and-ui.md) — to be revised against this ADR.