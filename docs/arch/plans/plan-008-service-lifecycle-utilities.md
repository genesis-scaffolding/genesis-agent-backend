# Plan-008: Framework-provided service lifecycle utilities

Step-by-step implementation. Each step is self-contained and testable.

---

## Phase 1: Core utilities (no plugin changes)

### Step 1 — `genesis_worker/utils/process/`

Create `genesis_worker/utils/process/__init__.py`:

```python
"""Process management helpers — tmux, process supervision."""

from .tmux import TmuxProcess

__all__ = ["TmuxProcess"]
```

Create `genesis_worker/utils/process/tmux.py`:

- Implement `TmuxProcess` as specified in `spec-008-service-lifecycle-utilities.md`.
- `has_session(name)`, `exists()`, `kill()`, `send_interrupt()`, `start()`, `stop()`.
- `start` wraps the command in `cmd 2>&1 | tee -a log_file`.
- Accepts `wait_for_children: Callable[[], bool] | None` for child-drain.
- Handles pre-existing session cleanup in `start`.

Run: `uv run pytest genesis_worker/tests/test_tmux_process.py -q` (write tests first).

### Step 2 — `genesis_worker/utils/net/`

Create `genesis_worker/utils/net/__init__.py`:

```python
"""Network helpers — HTTP probing."""

from .probe import HealthProbe

__all__ = ["HealthProbe"]
```

Create `genesis_worker/utils/net/probe.py`:

- Implement `HealthProbe` as specified.
- `resolve_connect_host`, `endpoint`, `probe`, `wait_ready`.
- Default `probe_path="/v1/models"`.

Run: `uv run pytest genesis_worker/tests/test_health_probe.py -q` (write tests first).

### Step 3 — `genesis_worker/utils/install/session.py`

Create `genesis_worker/utils/install/__init__.py` update to export:

```python
from .session import BackgroundInstallSession, _Canceled

__all__ = [...existing..., "BackgroundInstallSession", "_Canceled"]
```

Create `genesis_worker/utils/install/session.py`:

- `_Canceled` sentinel exception.
- `_SessionState` dataclass.
- `BackgroundInstallSession` base class with `_name` property and `_run_inner()` abstract method.
- Concrete `current_step`, `submit`, `cancel`, `wait`, `_publish`, `_run`.

Run: `uv run pytest genesis_worker/tests/test_background_install_session.py -q` (write tests first).

---

## Phase 2: Streamlit UI utilities

### Step 4 — `genesis_worker/utils/ui/_service_controls.py`

Create `genesis_worker/utils/ui/_service_controls.py`:

- `render_service_controls(svc, status, *, show_web_ui_link=True, key_prefix="")`.
- State badge, Start/Stop button, inline install via `render_inline_install`, Web UI link.
- Delegates to `worker.start_service(st.session_state["worker"])` and `worker.stop_service`.
- Reads `svc.is_available()`, `svc.primary_installable()`, `svc.web_ui_endpoint()` through the contract interface.

Update `genesis_worker/utils/ui/__init__.py` to re-export.

Run: `uv run pytest genesis_worker/tests/test_service_controls.py -q` (write tests first).

### Step 5 — `genesis_worker/utils/ui/_tail_log.py`

Create `genesis_worker/utils/ui/_tail_log.py`:

- `render_tail_log(svc, *, n_bytes=8192, key="")`.
- `st.fragment(run_every="2s")` calling `svc.tail_log(n_bytes)` and rendering in `st.code`.
- Handles empty log with `st.caption`.

Update `genesis_worker/utils/ui/__init__.py` to re-export.

Run: `uv run pytest genesis_worker/tests/test_tail_log.py -q` (write tests first).

---

## Phase 3: Refactor llama-swap service

### Step 6 — Refactor `genesis_worker/services/llama_swap/lifecycle.py`

Simplify to a thin wrapper around `TmuxProcess` and `HealthProbe`:

```python
from genesis_worker.contracts import ServiceState, ServiceStatus
from genesis_worker.utils.process import TmuxProcess
from genesis_worker.utils.net import HealthProbe

# Replace ~80 lines with ~25
def start_swap(binary, config, listen_addr, session_name, log_file, health_timeout_s):
    if not binary.is_file():
        return StartResult(ok=False, message=f"binary not found: {binary}")
    if not config.is_file():
        return StartResult(ok=False, message=f"config not found: {config}")

    tmux = TmuxProcess(session_name)
    tmux.kill()  # clean up any prior session

    cmd = (
        f"{shlex.quote(str(binary))} --config {shlex.quote(str(config))} "
        f"-listen {listen_addr} -watch-config"
    )
    result = tmux.start(cmd, log_file, wait_for_children=_no_llama_servers)
    if not result.ok:
        return result

    host, port_str = listen_addr.rsplit(":", 1)
    probe = HealthProbe(host, int(port_str), probe_path="/v1/models")
    if probe.wait_ready(health_timeout_s):
        return StartResult(ok=True, message=f"started {session_name}")
    return StartResult(ok=False, message=f"did not become ready in {health_timeout_s:.0f}s; see {log_file}")

def stop_swap(session_name, shutdown_timeout_s=30.0):
    return TmuxProcess(session_name).stop()

def is_running(session_name):
    return TmuxProcess(session_name).exists()

def status(session_name, listen_addr):
    host, port_str = listen_addr.rsplit(":", 1)
    probe = HealthProbe(host, int(port_str), probe_path="/v1/models")
    endpoint = f"http://{listen_addr}/v1"
    if not TmuxProcess(session_name).exists():
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    if probe.probe():
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)

def wait_ready(listen_addr, timeout_s):
    host, port_str = listen_addr.rsplit(":", 1)
    return HealthProbe(host, int(port_str), probe_path="/v1/models").wait_ready(timeout_s)
```

Keep `_no_llama_servers`, `_wait_for_children_gone` locally (they reference the `llama-server` process name, which is llama-swap-specific).

### Step 7 — Refactor `genesis_worker/services/llama_swap/installs.py`

Replace `_GithubReleaseInstallSession` with a subclass of `BackgroundInstallSession`:

```python
from genesis_worker.utils.install import BackgroundInstallSession, _Canceled

class _GithubReleaseInstallSession(BackgroundInstallSession):
    @property
    def _name(self) -> str:
        return self._backend.name

    def _run_inner(self) -> None:
        # existing _run_inner body, unchanged logic
        # replace self._publish(...) calls with direct self._publish(...)
        # replace "raise _Canceled()" with "raise _Canceled" (no parens needed)
        # replace "raise RuntimeError(...)" with "raise RuntimeError(...)"
```

All other classes (`GithubReleaseTarball`, `LlamaSwapBinary`, `LlamaServerCUDA`, `_UpstreamLlamaServerBinary`, `LlamaServerCPU`, `LlamaServerVulkan`) are unchanged.

### Step 8 — Refactor `genesis_worker/services/llama_swap/ui/status.py`

Replace the service-info section with the utility calls:

```python
from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

# Before (~30 lines):
with st.container(border=True):
    st.header("Service info")
    status = worker.service_status(SERVICE_NAME)
    if status.state.value == "running": ...
    ...

# After (~2 lines):
with st.container(border=True):
    st.header("Service info")
    render_service_controls(svc, worker.service_status(SERVICE_NAME), key_prefix="status-llama_swap")
    ...

# Before (~15 lines):
with st.container(border=True):
    st.subheader("Console")
    @st.fragment(run_every="2s") def _console(): ...
    _console()

# After (~2 lines):
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, key="llama_swap")
```

The variant selector and binaries list sections are retained (llama-swap-specific).

---

## Phase 4: Refactor cptr service

### Step 9 — Refactor `genesis_worker/services/cptr/lifecycle.py`

Simplify similarly:

```python
def start_cptr(*, binary, host, port, session_name, log_file, health_timeout_s):
    if not binary.is_file():
        return StartResult(ok=False, message=f"binary not found: {binary}")

    tmux = TmuxProcess(session_name)
    tmux.kill()

    cmd = (
        f"{shlex.quote(str(binary))} run "
        f"--host {shlex.quote(host)} --port {port}"
    )
    result = tmux.start(cmd, log_file)
    if not result.ok:
        return result

    probe = HealthProbe(host, port, probe_path="/")
    if probe.wait_ready(health_timeout_s):
        return StartResult(ok=True, message=f"started {session_name}")
    return StartResult(ok=False, message=f"did not become ready in {health_timeout_s:.0f}s; see {log_file}")

def stop_cptr(session_name):
    return TmuxProcess(session_name).stop()

def is_running(session_name):
    return TmuxProcess(session_name).exists()

def status(session_name, host, port):
    probe = HealthProbe(host, port, probe_path="/")
    endpoint = f"http://{HealthProbe.resolve_connect_host(host)}:{port}/"
    if not TmuxProcess(session_name).exists():
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    if probe.probe():
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)

def wait_ready(host, port, timeout_s):
    return HealthProbe(host, port, probe_path="/").wait_ready(timeout_s)
```

### Step 10 — Refactor `genesis_worker/services/cptr/install.py`

Replace `_UvToolInstallSession` with a subclass of `BackgroundInstallSession`:

```python
from genesis_worker.utils.install import BackgroundInstallSession

class _UvToolInstallSession(BackgroundInstallSession):
    @property
    def _name(self) -> str:
        return self._package_name

    def _run_inner(self) -> None:
        # existing _run_inner body
```

`CptrInstall` class unchanged.

### Step 11 — Refactor `genesis_worker/services/cptr/ui/status.py`

```python
from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

# Replace service-info section (~20 lines) with:
with st.container(border=True):
    st.header("Service info")
    render_service_controls(svc, worker.service_status(SERVICE_NAME), key_prefix="status-cptr")
    ...

# Replace console section (~10 lines) with:
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, key="cptr")
```

The listen address and version display sections are retained.

---

## Phase 5: Dashboard (framework)

### Step 12 — Refactor `genesis_worker/ui/dashboard.py`

Update the services section to use `render_service_controls`:

```python
from genesis_worker.utils.ui._service_controls import render_service_controls

# Inside the services loop, replace the ~15-line inline service info block
# with a single call:
render_service_controls(
    svc,
    status,
    show_web_ui_link=True,
    key_prefix=f"dash-{info.name}",
)
```

The "Admin" button and "Web UI" link are still rendered inline in the dashboard (not part of `render_service_controls` to keep it reusable on service pages where those buttons live elsewhere).

---

## Phase 6: Verification

### Step 13 — Run the full verification suite

```bash
uv run pytest -q
uv run pyright genesis_worker/utils/ genesis_worker/services/
uv run ruff check genesis_worker
```

All three must pass.

---

## Files changed summary

| File | Change |
|------|--------|
| `genesis_worker/utils/process/__init__.py` | Create |
| `genesis_worker/utils/process/tmux.py` | Create |
| `genesis_worker/utils/net/__init__.py` | Create |
| `genesis_worker/utils/net/probe.py` | Create |
| `genesis_worker/utils/install/__init__.py` | Update exports |
| `genesis_worker/utils/install/session.py` | Create |
| `genesis_worker/utils/ui/_service_controls.py` | Create |
| `genesis_worker/utils/ui/_tail_log.py` | Create |
| `genesis_worker/utils/ui/__init__.py` | Update exports |
| `genesis_worker/services/llama_swap/lifecycle.py` | Refactor (~80→~30 lines) |
| `genesis_worker/services/llama_swap/installs.py` | Replace session class (~80→~30 lines) |
| `genesis_worker/services/llama_swap/ui/status.py` | Use utilities |
| `genesis_worker/services/cptr/lifecycle.py` | Refactor (~70→~25 lines) |
| `genesis_worker/services/cptr/install.py` | Replace session class (~60→~25 lines) |
| `genesis_worker/services/cptr/ui/status.py` | Use utilities |
| `genesis_worker/ui/dashboard.py` | Use `render_service_controls` |
| `genesis_worker/tests/test_tmux_process.py` | Create |
| `genesis_worker/tests/test_health_probe.py` | Create |
| `genesis_worker/tests/test_background_install_session.py` | Create |
| `genesis_worker/tests/test_service_controls.py` | Create |
| `genesis_worker/tests/test_tail_log.py` | Create |
