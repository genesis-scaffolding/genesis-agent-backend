# Spec 003: Facade, CLI, and Streamlit app

Implements [ADR-010](../adr-010-per-plugin-ui-pages.md) (per-plugin UI pages, dashboard as control surface) atop [ADR-009](../adr-009-framework-plugin-boundary.md) (framework/plugin boundary).

## Goal

Ship a phone-reachable Streamlit UI for managing the worker:

- A control-surface dashboard with live system metrics and per-service tiles (Stop/Start, Admin, Web UI).
- Per-service management pages (config editor, recipes view, pi export for llama-swap today; landing pages for any future service).
- Per-source acquire wizards (HF today; structure ready for ModelScope, Civitai later).
- Thin CLI wrappers around the same facade (for Phase 10 retirement of `bin/`).

End-state: a phone browser on Tailscale reaches the worker at `:8501`, can swap which service is running to manage VRAM, can edit per-model overrides and regenerate `config.yaml`, view `recipes.yaml` read-only, download/install `pi-models.json`, and walk the HF acquire wizard.

## Architectural alignment

- ADR-010 — per-plugin UI pages; framework dashboard is a control surface; `has_web_ui` vs. management UI distinguished.
- ADR-009 — framework/plugin boundary. `contracts/` is the only shared surface.
- ADR-003 — `GenesisWorker` facade with capability-driven UI.
- ADR-004 — XDG paths; `Settings` carries plugin option slices.
- ADR-005 — HF acquire via `huggingface_hub`.
- ADR-006 — PyYAML everywhere.
- ADR-007 — overrides in `overrides.yaml`; no SQLite.
- ADR-008 — `bin/`, `Makefile`, repo-root state files untouched in v1.

## Modules added and modified

```
genesis_worker/
  contracts/
    ui.py                                ← new (UiPage dataclass)
    service.py                           ← modified: ui_pages on InferenceService ABC
    source.py                            ← modified: ui_pages on ModelSource ABC
  ui/
    dashboard.py                         ← new (framework-owned control surface)
    catalog.py                           ← new (framework-owned browse view)
  facade.py                              ← modified: start/stop/status/collect_metrics
  metrics/
    system.py                            ← new: collect_metrics() → MachineMetrics
  services/
    llama_swap/
      service.py                         ← modified: implements ui_pages
      ui/                                ← new (plugin-owned)
        status.py                        ← landing page
        config_editor.py                 ← override editing
        recipes_view.py                  ← read-only recipes
        pi_export.py                     ← pi-models export
  sources/
    huggingface/
      source.py                          ← modified: implements ui_pages
      ui/                                ← new (plugin-owned)
        acquire.py                       ← landing page (wizard entry)
        session_list.py                  ← in-flight sessions
  cli/
    ui.py                                ← new: console-script entry point for the UI
    up.py                                ← unchanged
    catalog.py                           ← unchanged
    config.py                            ← unchanged
    hf_model.py                          ← unchanged
    pi_models.py                         ← unchanged

pyproject.toml                            ← modified: [project.scripts] adds genesis-worker-ui

streamlit_app/                            ← REMOVED entirely
```

`streamlit_app/pages/` is also gone. The UI entry script lives inside the package so the same code path works for repo checkouts and installed wheels.

## `genesis_worker/contracts/ui.py` (new)

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UiPage:
    """A page a plugin contributes to the management UI.

    Streamlit executes the file at ``path`` as a script. The page reads
    ``st.session_state["worker"]`` to access the cached facade.
    """

    label: str          # sidebar text
    icon: str           # streamlit icon identifier (e.g. ":material/tune:")
    path: Path          # absolute path to the .py file
```

## `genesis_worker/contracts/service.py` (modified)

```python
@property
@abstractmethod
def ui_pages(self) -> list[UiPage]:
    """Pages this service contributes. Empty list = no management UI.
    First entry is the landing page (ADR-010)."""
```

## `genesis_worker/contracts/source.py` (modified)

Same property, added to `ModelSource`.

## `genesis_worker/metrics/system.py` (new)

`collect_metrics() -> MachineMetrics` using `psutil` for CPU/RAM and `pynvml` for GPU/VRAM.

```python
@dataclass(frozen=True)
class MachineMetrics:
    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    gpu_percent: float | None       # None if pynvml cannot find an NVIDIA driver
    vram_used_gb: float | None      # None if no NVIDIA driver
    vram_total_gb: float | None     # None if no NVIDIA driver
```

`psutil` for CPU/RAM always succeeds; `pynvml` is the only path that can return `None`. We import inside the function and swallow `NVMLException` so a missing driver is a graceful degradation, not a hard failure of the whole UI.

## `genesis_worker/facade.py` (modified)

Four additions:

```python
def start_service(self, name: str) -> StartResult:
    return self._service_registry.get(name).start()

def stop_service(self, name: str) -> StopResult:
    return self._service_registry.get(name).stop()

def service_status(self, name: str) -> ServiceStatus:
    return self._service_registry.get(name).status()

def collect_metrics(self) -> MachineMetrics:
    from .metrics.system import collect_metrics as _collect
    return _collect()
```

`StartResult`, `StopResult`, `ServiceStatus` are already in `contracts/`.

## `genesis_worker/services/llama_swap/service.py` (modified)

```python
@property
def ui_pages(self) -> list[UiPage]:
    ui_dir = Path(__file__).parent / "ui"
    return [
        UiPage("Status",        ":material/monitor:",    ui_dir / "status.py"),
        UiPage("Config editor", ":material/tune:",       ui_dir / "config_editor.py"),
        UiPage("Recipes view",  ":material/menu_book:",  ui_dir / "recipes_view.py"),
        UiPage("Pi export",     ":material/download:",   ui_dir / "pi_export.py"),
    ]
```

First entry is the landing page.

## Plugin pages for llama-swap

### `genesis_worker/services/llama_swap/ui/status.py`

The landing page. Renders:

- Service state, pid, uptime if running.
- `[ Stop ]` / `[ Start ]` button (inline, no navigation).
- Web UI link when `has_web_ui` and running.
- Config status indicator: `✓ generated 2h ago`, `⚠ not generated`, `⚠ stale (overrides changed)`.
- `[ ↻ Regenerate config ]` button.
- `[ Manage config → ]` button → `st.switch_page()` to the config editor.

Stop/Start is inlined (~5 lines). No framework UI helper.

### `genesis_worker/services/llama_swap/ui/config_editor.py`

Override editing only. For each catalog entry: an `st.expander` with current effective config + an "Override" toggle + override fields. Saves to `service.overrides_store()` on the service. Does not call `regenerate_config` — that lives on the landing page.

### `genesis_worker/services/llama_swap/ui/recipes_view.py`

Read-only. For each recipe, an `st.expander` with `st.code(yaml.safe_dump(recipe.model_dump(), sort_keys=False), language="yaml")`.

### `genesis_worker/services/llama_swap/ui/pi_export.py`

- Preview button → `service.export_for_agent()` → `st.code(json.dumps(...))`.
- `st.download_button("Download", ...)` for the JSON.
- "Install to `~/.pi/agent/models.json`" button → `service.write_agent_config(target)`.

## `genesis_worker/sources/huggingface/source.py` (modified)

```python
@property
def ui_pages(self) -> list[UiPage]:
    ui_dir = Path(__file__).parent / "ui"
    return [
        UiPage("Acquire model",   ":material/cloud_download:", ui_dir / "acquire.py"),
        UiPage("Active sessions", ":material/schedule:",       ui_dir / "session_list.py"),
    ]
```

## Plugin pages for HuggingFace

### `genesis_worker/sources/huggingface/ui/acquire.py`

The landing page for the source. Two modes:

- **No active session:** form (repo id input, optional filters, Start button) → `worker.start_acquire(source_name, repo_id)`. Stores the returned session id in `st.session_state[f"acquire_sid_{src.name}"]`.
- **Active session:** render the wizard for `worker.acquire_step(sid)`. HF wizard shape: `select_files` (form with file groups) → `confirm_storage` (warning + Confirm button) → `downloading` (progress bar + log tail + Cancel) → `complete` / `failed` / `cancelled`.

Polling during `downloading` uses `time.sleep(2) + st.rerun()`.

### `genesis_worker/sources/huggingface/ui/session_list.py`

Lists all in-flight acquire sessions for this source. Read-only with a Cancel button per session. Source data: `worker.list_acquire_sessions()` filtered by source name.

## Framework UI pages

### `genesis_worker/ui/dashboard.py`

Three sections, top to bottom:

**System strip** — `worker.collect_metrics()` rendered as four `st.metric` calls (CPU %, RAM used/total, GPU %, VRAM used/total). GPU/VRAM cells show "n/a" when the corresponding fields are `None`. Refreshed on a 10s interval via `time.sleep + st.rerun()`.

**Services grid** — iterates `worker.services.all()`. For each service, renders a tile:

```
┌──────────────────────────────────┐
│ llama-swap        ● RUNNING      │
│ Inference service                │
│ ~12 GB VRAM                      │
│                                  │
│ [ Stop ]   [ Admin ]   [ Web UI ↗]│
└──────────────────────────────────┘
```

- Status indicator (`worker.service_status(name).state`).
- VRAM estimate from `worker.service(name).resource_estimate().vram_bytes_typical`.
- `[ Stop ]` / `[ Start ]` — calls `worker.start_service(name)` or `worker.stop_service(name)`, then `st.rerun()`.
- `[ Admin ]` — `st.switch_page()` to the first entry of `service.ui_pages`.
- `[ Web UI ↗ ]` — `st.link_button()` to `service.status().endpoint`. Only when the service's `capabilities().has_web_ui` is True AND state is RUNNING AND `endpoint` is set.

Tile rendering uses `st.container(border=True)` with `st.columns` for layout. Service display name from `worker.service_info(name).display_name`.

**Vault section** — two sub-widgets:

- Tabbed catalog: one tab per source (`worker.sources.all()`). Each tab shows the catalog entries for that source, expanded on click.
- Acquire widget: `st.selectbox` of sources where `src.can_acquire`, then `st.button("Go")` that `st.switch_page()` to the source's first `ui_pages` entry. If only one source can acquire, no selectbox — straight to its page.

### `genesis_worker/ui/catalog.py`

Single screen. `st.dataframe` of `worker.catalog()` entries with optional detail expansion. Rescan button at top. Read-only browse view; the dashboard's vault section is the glance.

## `genesis_worker/ui/app.py` (new location)

The Streamlit entry script. Lives inside the package so the same code path works for repo checkouts (`uv run`) and installed wheels (`genesis-worker-ui` console script).

```python
"""Streamlit app shell for the Genesis Worker."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from genesis_worker import GenesisWorker

_FRAMEWORK_UI = Path(__file__).parent


@st.cache_resource
def get_worker() -> GenesisWorker:
    return GenesisWorker()


worker = get_worker()
st.session_state["worker"] = worker

st.set_page_config(page_title="Genesis Worker", layout="wide",
                    page_icon=":material/settings:")


def _page(path: Path, title: str, icon: str) -> st.Page:
    return st.Page(str(path), title=title, icon=icon)


nav: dict[str, list[st.Page]] = {
    "Overview": [
        _page(_FRAMEWORK_UI / "dashboard.py", "Dashboard", ":material/dashboard:"),
        _page(_FRAMEWORK_UI / "catalog.py",   "Catalog",   ":material/folder:"),
    ],
}

for svc in worker.services.all():
    nav[svc.display_name] = [
        _page(p.path, p.label, p.icon) for p in svc.ui_pages
    ]

for src in worker.sources.all():
    nav[src.display_name] = [
        _page(p.path, p.label, p.icon) for p in src.ui_pages
    ]

st.navigation(nav).run()
```

`Path(__file__).parent` resolves to `genesis_worker/ui/`, which is also where `dashboard.py` and `catalog.py` live. No `parent.parent` gymnastics; the entry script and its framework pages share a directory.

## `genesis_worker/cli/ui.py` (new)

The console-script target. Resolves the `app.py` path from `__file__` and shells out to `streamlit run`.

```python
"""Console-script entry point for the Streamlit UI."""

import os
import subprocess
import sys

from ..ui.app import __file__ as APP_PATH


def main() -> int:
    """Launch the Streamlit UI server."""
    port = os.environ.get("GENESIS_UI_PORT", "8501")
    return subprocess.call([
        sys.executable, "-m", "streamlit", "run", APP_PATH,
        "--server.address", "0.0.0.0",
        "--server.port", port,
        "--server.headless", "true",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
```

## `pyproject.toml` (modified)

Add a `[project.scripts]` entry:

```toml
[project.scripts]
genesis-worker-ui = "genesis_worker.cli.ui:main"
```

After `uv sync` (or `pip install`), the `genesis-worker-ui` executable lands on PATH. Repo-checkout launch is `uv run genesis-worker-ui`. Installed launch is `genesis-worker-ui`. Both resolve to the same `app.py` via `__file__`.

## CLI wrappers (unchanged from previous spec-003)

Five CLI modules under `genesis_worker/cli/`:

- `up.py` — `worker.start_service(args.service)` / `worker.stop_service(args.service)`.
- `catalog.py` — `worker.rescan_catalog()`.
- `config.py` — `worker.service("llama-swap").regenerate_config()`.
- `hf_model.py` — REPL driving `HfAcquireSession` from stdin.
- `pi_models.py` — `worker.service("llama-swap").export_for_agent()` + `write_models_json`.

Wired into the Makefile only during Phase 10 retirement (post-v1, ADR-008).

## State model

| What | Where |
|---|---|
| `GenesisWorker` instance | `@st.cache_resource` in `genesis_worker/ui/app.py` |
| Worker reference | `st.session_state["worker"]` (set once, read by all pages) |
| Acquire sessions | `worker._acquire_sessions` (on the cached instance) |
| Per-plugin UI state (overrides drafts, etc.) | Plugin's store on the service instance |
| Per-page ephemera (active acquire sid, etc.) | `st.session_state[f"..."]` |

The framework never holds plugin state. Plugins hold their own state on the service/source instance. Streamlit holds page-navigation ephemera.

## Polling and refresh

- Dashboard auto-refresh: 10s (`time.sleep + st.rerun()`).
- Acquire progress: 2s while a session is `downloading`.
- Service state transitions (STARTING / STOPPING): 1s for the first 30s, then stable.

v2 upgrade path: `st.fragment(run_every=timedelta(...))` for background refresh without blocking.

## Verification

1. `uv run pytest -q` passes (existing 181 + new tests).
2. `uv run pyright` 0 errors.
3. `uv run ruff check genesis_worker` clean.
4. `uv run python -c "from genesis_worker import GenesisWorker; w = GenesisWorker(); print(w.list_services())"` prints at least `llama-swap`.
5. `uv run python -c "from genesis_worker import GenesisWorker; w = GenesisWorker(); print(w.collect_metrics())"` returns a `MachineMetrics` with `cpu_percent`, `ram_used_gb`, `ram_total_gb` populated; `gpu_percent` and `vram_*` may be `None`.
6. `uv run genesis-worker-ui` starts; `curl -s http://127.0.0.1:8501/_stcore/health` returns 200.
7. **Phone end-to-end from Tailscale:**
   1. Open dashboard. See system strip (CPU/RAM/VRAM), tiles for each service, vault section.
   2. See llama-swap RUNNING tile with `[ Stop ] [ Admin ] [ Web UI ↗ ]` buttons.
   3. Tap `[ Stop ]`. State flips to STOPPED within 60s. Web UI button disappears.
   4. Tap `[ Start ]`. State flips to RUNNING within 60s. Web UI button reappears.
   5. Tap `[ Admin ]`. Lands on llama-swap Status page.
   6. From Status page: see config status, tap `[ ↻ Regenerate config ]`, observe success.
   7. Tap `[ Manage config → ]`. Lands on Config editor. Toggle an override. Save.
   8. Back to Status page; observe stale badge.
   9. Tap Recipes view. See structured recipes.
   10. Tap Pi export. Preview → Download → Install.
   11. Back to dashboard. Tap vault section's Acquire widget → HuggingFace → Go. Lands on HF Acquire page.
   12. Start an acquire with a small repo; walk through file selection → confirm → progress → complete.
   13. Rescan catalog; observe new entry under HF tab.
8. `make all` still passes; `config.yaml`, `pi-models.json`, `MODEL_CATALOG.*` unchanged after the dashboard run (Streamlit only writes when its buttons are explicitly clicked).
9. The running llama-swap is unaffected by Streamlit startup, shutdown, or page navigation.

## Tests

- `test_facade.py` — instantiate `GenesisWorker(Settings())`. `list_services()` returns llama-swap. `service("llama-swap").ui_pages` returns 4 entries with paths inside the plugin's `ui/` directory. `start_service("llama-swap")` returns a `StartResult`. `stop_service("llama-swap")` returns a `StopResult`. `service_status("llama-swap")` returns a `ServiceStatus`. `collect_metrics()` returns a `MachineMetrics`.
- `test_metrics_system.py` — `collect_metrics()` returns non-None CPU/RAM. Mock `psutil`/`pynvml` for predictable values. GPU/VRAM `None` path exercised.
- `test_cli_smoke.py` — `python -m genesis_worker.cli.up --help`, `--config`, `--catalog`, `--hf_model`, `--pi_models` all exit 0.
- `test_ui_pages.py` — for each registered service/source, `svc.ui_pages` is a list of `UiPage`. Each `UiPage.path` resolves inside the plugin's `ui/` directory and exists on disk. The first entry's path matches the landing convention.
- `test_app_shell.py` — smoke test that constructs `GenesisWorker`, calls the page-discovery code from `genesis_worker.ui.app` in isolation, and asserts each page's path exists.

Streamlit pages are not unit-tested (Streamlit's testing harness is heavy). Verified by the end-to-end phone scenario above.

## Open issues deferred to v2

- `st.fragment(run_every=...)` for non-blocking refresh.
- Per-service UI widgets (e.g. a "service status card" component) if duplication becomes painful.
- Authentication (Tailscale ACL + host firewall are the only access control in v1).
- Real-time VRAM conflict detection (Start buttons stay always-enabled; user is responsible, per spec-003).