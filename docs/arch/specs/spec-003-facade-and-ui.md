# Spec 003: Facade, CLI, and Streamlit app

## Goal
Implement ADR-003 (the facade), and ship the Streamlit UI that drives it. End-state: a phone browser on Tailscale reaches the worker at `:8501` and can stop/start llama-swap, rescan the catalog, drive the HF acquire wizard, edit per-model overrides and regenerate `config.yaml`, view `recipes.yaml` read-only, and download/install `pi-models.json`.

This spec covers Phases 8–9 of the master plan (and references Phase 10 retirement, which is post-v1 and listed for completeness).

## Modules added

```
genesis_worker/
├── facade.py                       # GenesisWorker — single public API
├── cli/
│   ├── __init__.py
│   ├── catalog.py                  # thin wrapper
│   ├── config.py                   # thin wrapper
│   ├── up.py                       # thin wrapper
│   ├── hf_model.py                 # thin wrapper (drives HfAcquireSession in REPL form)
│   └── pi_models.py                # thin wrapper

streamlit_app/
├── app.py                          # multipage entry
├── pages/
│   ├── dashboard.py
│   ├── catalog.py
│   ├── acquire.py
│   ├── config_editor.py
│   ├── recipes_view.py
│   └── pi_export.py
└── run.sh                          # convenience wrapper: streamlit run ... --server.address 0.0.0.0
```

## Dependencies

```bash
uv add streamlit
```

`huggingface_hub`, `pydantic`, `pydantic-settings`, `pyyaml`, `psutil`, `pynvml` were added in specs 001 and 002.

## `genesis_worker/facade.py`

```python
from __future__ import annotations

import uuid
from pathlib import Path

from .catalog.build import CatalogService
from .catalog.schema import Catalog
from .metrics.system import collect_metrics, MachineMetrics
from .services._base import (
    InferenceService,
    ServiceCapabilities,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
)
from .services._registry import all_services
from .settings import Settings
from .sources._base import AcquireChoice, AcquireSession, AcquireStep
from .sources._registry import all_sources


class GenesisWorker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._catalog: Catalog | None = None
        self._acquire_sessions: dict[str, AcquireSession] = {}

    # --- catalog ---
    def list_sources(self) -> list: ...  # source info dataclass
    def rescan_catalog(self) -> Catalog: ...
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = self.rescan_catalog()
        return self._catalog

    # --- services ---
    def list_services(self) -> list: ...
    def service(self, name: str) -> InferenceService: ...
    def start_service(self, name: str) -> StartResult: ...
    def stop_service(self, name: str) -> StopResult: ...
    def service_status(self, name: str) -> ServiceStatus: ...
    def service_capabilities(self, name: str) -> ServiceCapabilities: ...
    def service_resource_estimate(self, name: str) -> ServiceResourceEstimate: ...

    # --- acquire ---
    def start_acquire(self, source_name: str, repo_id: str, **kw) -> str:
        session = _make_session(source_name, repo_id, self.settings, **kw)
        sid = str(uuid.uuid4())
        self._acquire_sessions[sid] = session
        session.current_step()  # trigger inspecting
        return sid

    def acquire_step(self, session_id: str) -> AcquireStep: ...
    def submit_acquire(self, session_id: str, choice: AcquireChoice) -> AcquireStep: ...
    def cancel_acquire(self, session_id: str) -> None: ...
    def list_acquire_sessions(self) -> list[dict]: ...

    # --- metrics ---
    def collect_metrics(self) -> MachineMetrics: ...
```

`_make_session` looks up the source class from `all_sources()` and constructs its `AcquireSession`. Today only HF has one.

## CLI wrappers

Each `genesis_worker/cli/<x>.py` is ~10 lines:

```python
# cli/up.py
import argparse
from ..facade import GenesisWorker


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--service", default="llama-swap")
    args = p.parse_args()
    w = GenesisWorker()
    print(w.start_service(args.service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The other four (`catalog.py`, `config.py`, `hf_model.py`, `pi_models.py`) follow the same shape. They are **not wired into the Makefile** during v1 — the Makefile keeps calling `bin/`. They exist for Phase 10 retirement.

## Streamlit app

### `streamlit_app/app.py`

```python
import streamlit as st

from genesis_worker import GenesisWorker


@st.cache_resource
def get_worker() -> GenesisWorker:
    return GenesisWorker()


st.set_page_config(page_title="Genesis Worker", layout="wide")
worker = get_worker()
st.session_state["worker"] = worker
st.title("Genesis Worker")
st.caption(f"Vault: {worker.settings.paths.resolved_vault_path}")
```

(`pages/` files are auto-discovered by Streamlit's multipage-app convention.)

### `streamlit_app/pages/dashboard.py`

```python
import streamlit as st

worker = st.session_state["worker"]

st.header("Services")
for info in worker.list_services():
    svc = worker.service(info.name)
    status = worker.service_status(info.name)
    cols = st.columns([2, 1, 1, 2, 1])
    with cols[0]:
        st.write(f"**{info.display_name}**")
    with cols[1]:
        st.write(f"`{status.state.value}`")
    with cols[2]:
        if status.endpoint:
            st.link_button("UI", status.endpoint)
    with cols[3]:
        est = worker.service_resource_estimate(info.name)
        st.write(f"~{est.vram_bytes_typical / 1e9:.1f} GB VRAM")
    with cols[4]:
        if status.state.value == "running":
            if st.button("Stop", key=f"stop-{info.name}"):
                worker.stop_service(info.name)
                st.rerun()
        else:
            if st.button("Start", key=f"start-{info.name}"):
                worker.start_service(info.name)
                st.rerun()

st.header("Metrics")
m = worker.collect_metrics()
cols = st.columns(4)
cols[0].metric("CPU", f"{m.cpu_percent:.0f}%")
cols[1].metric("RAM", f"{m.ram_used_gb:.1f}/{m.ram_total_gb:.1f} GB")
cols[2].metric("GPU", f"{m.gpu_percent:.0f}%" if m.gpu_percent is not None else "n/a")
cols[3].metric(
    "VRAM", f"{m.vram_used_gb:.1f}/{m.vram_total_gb:.1f} GB" if m.vram_total_gb else "n/a"
)
```

### `streamlit_app/pages/catalog.py`

- `st.button("Rescan")` → `worker.rescan_catalog()`.
- Tabs: HuggingFace | LM Studio.
- For each entry: `st.expander(entry.name)` showing pieces (main, mmproj, mtp), total size, directory.

### `streamlit_app/pages/acquire.py`

Generic `AcquireStep` renderer:

```python
worker = st.session_state["worker"]
sid = st.session_state.get("acquire_sid")

if not sid:
    with st.form("acquire-start"):
        source = st.selectbox("Source", [s.name for s in worker.list_sources()])
        repo = st.text_input("Repo (org/name)")
        if st.form_submit_button("Start") and repo:
            sid = worker.start_acquire(source, repo)
            st.session_state["acquire_sid"] = sid
            st.rerun()
else:
    step = worker.acquire_step(sid)
    st.subheader(step.title)
    if step.kind == "select_files" and step.file_groups:
        # render file_groups as a form, capture main + aux choice
        ...
    elif step.kind == "confirm_storage":
        st.warning(f"Will download {_fmt(step.total_bytes)}")
        if st.button("Confirm"):
            worker.submit_acquire(sid, AcquireChoice(confirm=True))
            st.rerun()
    elif step.kind == "downloading":
        if step.progress:
            st.progress(step.progress.bytes_done / step.progress.bytes_total)
        if step.log_tail:
            st.code("\n".join(step.log_tail[-10:]))
        if st.button("Cancel"):
            worker.cancel_acquire(sid)
        import time

        time.sleep(2)
        st.rerun()
    elif step.kind in ("complete", "failed", "cancelled"):
        st.write(step.kind)
        if st.button("Done"):
            del st.session_state["acquire_sid"]
            st.rerun()
```

### `streamlit_app/pages/config_editor.py`

- For each catalog entry: an `st.expander` showing the recipe values (read-only) and the override fields (each is an `st.checkbox` "Override" + a value input).
- A "stale" indicator at the top if `is_config_stale()` returns True.
- "Regenerate" button at the top → `service.regenerate_config()` → `st.success("regenerated")`.

### `streamlit_app/pages/recipes_view.py`

- For each recipe: an `st.expander` showing fields in structured form (`st.code(yaml.safe_dump(recipe.model_dump(), sort_keys=False), language="yaml")` or labeled `st.metric` blocks).
- Read-only.

### `streamlit_app/pages/pi_export.py`

- "Preview" button → `service.export_for_agent()` → `st.code(json.dumps(...))`.
- `st.download_button("Download", ...)` for the JSON file.
- "Install to ~/.pi/agent/models.json" button → `install_models_json()`.

### `streamlit_app/run.sh`

```bash
#!/usr/bin/env bash
exec uv run streamlit run streamlit_app/app.py \
    --server.address 0.0.0.0 \
    --server.port "${GENESIS_UI_PORT:-8501}" \
    "$@"
```

## Launching

```bash
# Tailscale interface is up; firewall / Tailscale ACL lets the phone reach :8501.
./streamlit_app/run.sh
```

## Tests

- `test_facade.py`: instantiate `GenesisWorker(Settings())`; `list_services()` returns llama-swap; `service("llama-swap").capabilities().can_serve_llm == True`; `start_acquire("huggingface", "test/repo")` returns a session id; `acquire_step(id)` returns an `inspecting` step.
- `test_cli_smoke.py`: `python -m genesis_worker.cli.up --help` exits 0; `python -m genesis_worker.cli.catalog --help` exits 0.
- Streamlit pages: not unit-tested (Streamlit's testing harness is heavy). Verified by the end-to-end phone-browser scenario in Verification.

## Verification

1. `uv run pytest genesis_worker/tests/` passes.
2. `uv run python -c "from genesis_worker import GenesisWorker; w = GenesisWorker(); print(w.list_services())"` prints at least `llama-swap`.
3. `uv run python -m genesis_worker.cli.catalog --help`, `--config`, `--up --help`, etc. all exit 0.
4. `./streamlit_app/run.sh` starts; `curl -s http://127.0.0.1:8501/_stcore/health` returns 200.
5. **End-to-end from a phone browser on Tailscale:**
   1. Open dashboard; see llama-swap as RUNNING with a stop button and a link to its UI.
   2. Stop llama-swap; status flips to STOPPED.
   3. Start llama-swap; status flips to RUNNING within 60s.
   4. Open the catalog page; click Rescan; entries appear under HuggingFace and LM Studio tabs.
   5. Open the acquire page; pick HuggingFace; paste `unsloth/Qwen3.5-9B-MTP-GGUF` (or a real repo of choice); walk through file selection → confirm → progress → complete. Files appear under the HF cache.
   6. Open the config editor; toggle an override on one entry; click Regenerate; the resolved `config.yaml` on disk reflects the override; llama-swap hot-reloads.
   7. Open the recipes view; structured read-only rendering of `recipes.yaml`.
   8. Open the pi export page; click Preview → Download → Install to `~/.pi/agent/models.json`.
6. `make all` still passes; `config.yaml`, `pi-models.json`, `MODEL_CATALOG.*` unchanged after the dashboard run (Streamlit only writes when its buttons are explicitly clicked).
7. The running llama-swap is unaffected by the Streamlit app's startup, shutdown, or page navigation. (No restart triggered by the UI.)

## Post-v1 (Phase 10 retirement, referenced from ADR-008)

For each `bin/` script, one at a time:

1. Replace its body with `from genesis_worker.cli.<x> import main; raise SystemExit(main())`.
2. Run `make all`; confirm content-equivalent output.
3. Delete the script.
4. Update the `Makefile` to point at the new entry point.

Order: `pi_models.py` → `catalog.py` → `build_config.py` → `hf_model.py` → `up` (last). `bin/bonsai-server` moves under `scripts/dev/` and stays.
