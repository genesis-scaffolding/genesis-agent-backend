# Spec-008: Framework-provided service lifecycle utilities

## Overview

Four utilities provided by the framework to eliminate per-service duplication:

1. `TmuxProcess` — tmux session lifecycle
2. `HealthProbe` — HTTP readiness polling
3. `BackgroundInstallSession` — streaming install session base class
4. `render_service_controls()` + `render_tail_log()` — Streamlit UI helpers

All live under `genesis_worker/utils/`.

---

## 1. `genesis_worker/utils/process/tmux.py`

### Module docstring

"""Tmux session lifecycle — start, stop, interrupt, and probe a named session."""

### Class: `TmuxProcess`

```python
from pathlib import Path
from typing import Callable

from genesis_worker.contracts import StartResult, StopResult

class TmuxProcess:
    def __init__(self, session_name: str) -> None:
        self._session_name = session_name
```

**Methods:**

```python
@staticmethod
def has_session(name: str) -> bool:
    """True iff a tmux session named ``name`` exists."""

def exists(self) -> bool:
    """True iff this instance's session exists. Alias for has_session(name)."""

def kill(self) -> None:
    """Kill the tmux session unconditionally. No-op if not running."""

def send_interrupt(self) -> None:
    """Send Ctrl-C to the foreground process group in the session.
    
    tmux routes the keypress to the pane's active process. For a pipeline
    (bash running `binary ... | tee ...`), bash receives SIGINT and propagates
    it to its children. Use this before wait_for_children.
    """

def start(
    self,
    cmd: str,
    log_file: Path,
    *,
    wait_for_children: Callable[[], bool] | None = None,
    child_wait_timeout_s: float = 30.0,
) -> StartResult:
    """Start ``cmd`` in a new detached tmux session.
    
    The command is always wrapped as:
        {cmd} 2>&1 | tee -a {log_file}
    
    ``wait_for_children`` is a predicate called after SIGINT to confirm
    child processes have exited. When not None, ``start`` polls the
    predicate for up to ``child_wait_timeout_s`` before force-killing
    any remaining children with SIGKILL.
    
    ``log_file`` parent is created if absent.
    
    Returns ``StartResult`` with ``ok=False`` and a message on failure.
    """

def stop(self) -> StopResult:
    """Stop the session gracefully.
    
    Calls ``send_interrupt()``, then polls ``wait_for_children`` (if set)
    for ``child_wait_timeout_s``. Force-kills any remaining children with
    SIGKILL. Tears down the tmux session.
    
    Returns ``StopResult``.
```

**Implementation notes:**

- `has_session` and `exists` use `tmux has-session -t <name>` (return code 0 = exists).
- `start` writes the wrapped command to a shell string and calls `tmux new-session -d -s <name> <shell_cmd>`.
- `kill` calls `tmux kill-session -t <name>`.
- `send_interrupt` calls `tmux send-keys -t <name> C-c`.
- `start` cleans up any prior session of the same name before creating the new one.
- The wrapped shell string: `f"{shlex.quote(cmd)} 2>&1 | tee -a {shlex.quote(str(log_file))}"`
- `wait_for_children` is called in a loop with 0.5s sleep. Timeout falls through to SIGKILL + force cleanup.
- All subprocess calls use `check=False`, `capture_output=True`, `text=True`.
- `child_wait_timeout_s` default 30.0s matches the existing llama-swap value.

### Imports

```python
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable, ClassVar
```

---

## 2. `genesis_worker/utils/net/probe.py`

### Module docstring

"""HTTP readiness probing — single probe and polling loop."""

### Class: `HealthProbe`

```python
import urllib.error
import urllib.request

from genesis_worker.contracts import ServiceState, ServiceStatus

class HealthProbe:
    DEFAULT_PROBE_PATH: ClassVar[str] = "/v1/models"
```

**Constructor:**

```python
def __init__(
    self,
    host: str,
    port: int,
    *,
    probe_path: str = DEFAULT_PROBE_PATH,
) -> None:
    self._host = host
    self._port = port
    self._probe_path = probe_path  # e.g. "/" for root-only services
```

**Methods:**

```python
@staticmethod
def resolve_connect_host(host: str) -> str:
    """Translate a bind address into a connectable address.
    
    0.0.0.0 and :: are bind-only; clients must connect via 127.0.0.1.
    All other hosts are returned unchanged.
    """

@property
def endpoint(self) -> str:
    """The base URL without the probe path, e.g. 'http://hostname:8080/'."""

def probe(self) -> bool:
    """Single synchronous HTTP probe. Returns True iff the response is HTTP 200."""

def wait_ready(self, timeout_s: float) -> bool:
    """Poll ``probe()`` until it returns True or ``timeout_s`` elapses.
    
    Polls at 1.0s intervals. Returns True on success, False on timeout.
    """
```

**Constants:**

```python
_DEFAULT_POLL_S: ClassVar[float] = 1.0
_DEFAULT_TIMEOUT_PER_PROBE: ClassVar[float] = 1.0
```

**Implementation notes:**

- `resolve_connect_host` returns `"127.0.0.1"` for `("0.0.0.0", "::")`, else the input unchanged.
- `probe` calls `urllib.request.urlopen(f"http://{resolve_connect_host(self._host)}:{self._port}{self._probe_path}", timeout=self.DEFAULT_TIMEOUT_PER_PROBE)`. Returns `True` on status 200, `False` on any exception (`URLError`, `ConnectionError`, `TimeoutError`, `OSError`).
- `wait_ready` uses `time.monotonic()` for the deadline.
- The `endpoint` property strips the probe path so callers can use it for display: `http://host:port/` without a trailing path segment.

---

## 3. `genesis_worker/utils/install/session.py`

### Module docstring

"""Background install session — streaming state-machine base for plugin install backends."""

### Sentinel: `_Canceled`

```python
class _Canceled(Exception):
    """Raised by ``_run_inner`` to signal cancellation.
    
    Caught by the thread supervisor and translated to a 'cancelled' AcquireStep.
    """
```

### Class: `BackgroundInstallSession`

```python
import threading
from dataclasses import dataclass

from genesis_worker.contracts import AcquireChoice, AcquireStep, InstallSession

@dataclass
class _SessionState:
    step: AcquireStep
    canceled: bool = False
    done: bool = False
```

**Constructor:**

```python
def __init__(self) -> None:
    self._state = _SessionState(
        step=AcquireStep(kind="fetching", title=f"installing {self._name}")
    )
    self._cancel = threading.Event()
    self._done = False
    self._thread = threading.Thread(target=self._run, daemon=True)
    self._thread.start()
```

**Abstract property:**

```python
@property
@abstractmethod
def _name(self) -> str:
    """Human-readable name of the thing being installed. Used in the initial step title."""
    ...
```

**Abstract method:**

```python
@abstractmethod
def _run_inner(self) -> None:
    """Perform the install.
    
    Check ``self._cancel.is_set()`` between long-running steps (e.g. between
    files, between fetch and extract).
    
    On success: call ``self._publish(AcquireStep(kind='complete', ...))``.
    On failure: raise an exception (any subclass of ``Exception``).
    On cancellation: raise ``_Canceled``.
    
    The thread supervisor catches all of these and publishes the appropriate step.
    """
```

**Concrete methods:**

```python
def _publish(self, step: AcquireStep) -> None:
    """Thread-safe: assign to self._state.step."""
    self._state.step = step

def _run(self) -> None:
    """Thread target. Catches _Canceled and Exception; publishes final step."""
    try:
        self._run_inner()
    except _Canceled:
        self._publish(AcquireStep(kind="cancelled", title="cancelled", can_cancel=False))
    except Exception as exc:  # noqa: BLE001
        self._publish(
            AcquireStep(
                kind="failed",
                title=f"install failed: {exc}",
                error=str(exc),
            )
        )
    finally:
        self._state.done = True

# InstallSession protocol

def current_step(self) -> AcquireStep:
    return self._state.step

def submit(self, choice: AcquireChoice) -> AcquireStep:
    return self._state.step  # no user input expected during install; placeholder

def cancel(self) -> None:
    self._cancel.set()

def wait(self) -> AcquireStep:
    self._thread.join()
    return self._state.step
```

### Cancellation protocol

`cancel()` sets `self._cancel`. The subclass's `_run_inner` must check `self._cancel.is_set()` at appropriate points and raise `_Canceled` when it fires. The framework does not forcibly interrupt downloads or subprocess calls — the subclass cooperates.

---

## 4. `genesis_worker/utils/ui/_service_controls.py`

### Module docstring

"""Streamlit service controls — badge, Start/Stop, inline install, and Web UI link."""

### Function: `render_service_controls`

```python
import streamlit as st

from genesis_worker.contracts import InferenceService, ServiceStatus
from genesis_worker.utils.ui._install_flow import render_inline_install
```

```python
def render_service_controls(
    svc: InferenceService,
    status: ServiceStatus,
    *,
    show_web_ui_link: bool = True,
    key_prefix: str = "",
) -> None:
    """Render service info: state badge, Start/Stop, inline install, Web UI link.
    
    ``key_prefix`` namespaces Streamlit widget keys to avoid collisions when
    multiple instances appear on the same page.
    
    Assumes the caller has already fetched ``worker.service_status(name)`` and
    holds it in ``status``. Reads ``svc.is_available()`` and ``svc.web_ui_endpoint()``.
    
    The block is intentionally uncontainered so callers can wrap it in their
    own layout. Use ``with st.container(border=True):`` at the call site for
    a bordered appearance.
    """
```

**Implementation:** Mirrors the service-info section of both existing `ui/status.py` files:

1. `st.badge("Running", color="green")` or `st.badge("Stopped", color="gray")`
2. If running: `st.button("Stop", key=f"{key_prefix}-stop")` → `worker.stop_service(svc.name)` → `st.rerun()`
3. If not available and `svc.primary_installable()` is not None: `render_inline_install(primary, key_prefix=f"{key_prefix}-install")`
4. If not available and no installable: `st.caption("Not installed")`
5. If stopped and available: `st.button("Start", key=f"{key_prefix}-start")` → `worker.start_service(svc.name)` → `st.rerun()`
6. If running and `show_web_ui_link`: `st.link_button("Open Web UI", endpoint)`

---

## 5. `genesis_worker/utils/ui/_tail_log.py`

### Module docstring

"""Streamlit live console tail — auto-refreshing fragment for a service's log file."""

### Function: `render_tail_log`

```python
import streamlit as st

from genesis_worker.contracts import InferenceService
```

```python
def render_tail_log(
    svc: InferenceService,
    *,
    n_bytes: int = 8192,
    key: str = "",
) -> None:
    """Render an auto-refreshing console tail for ``svc``'s log file.
    
    ``n_bytes`` is the number of bytes to read from the end of the log file.
    ``key`` namespaces the fragment so multiple instances on the same page
    don't collide.
    
    The caller is responsible for wrapping in ``st.container(border=True)``
    and adding a subheader.
    """
```

**Implementation:**

```python
@st.fragment(run_every="2s", key=f"tail-log-{key}")
def _tail() -> None:
    content = svc.tail_log(n_bytes)
    if content:
        st.code(content, language=None)
    else:
        st.caption("No log output yet.")

_tail()
```

---

## File locations

```
genesis_worker/
  utils/
    process/
      __init__.py          # exports TmuxProcess
      tmux.py              # TmuxProcess
    net/
      __init__.py          # exports HealthProbe
      probe.py             # HealthProbe
    install/
      __init__.py          # exports BackgroundInstallSession, _Canceled
      session.py           # BackgroundInstallSession, _Canceled, _SessionState
    ui/
      __init__.py          # re-exports everything from _service_controls and _tail_log
      _service_controls.py # render_service_controls
      _tail_log.py         # render_tail_log
```

---

## Backward compatibility

All utilities are additive. Existing `lifecycle.py`, `install.py`, and `ui/status.py` files in plugins are refactored to use the utilities but retain their signatures for the service class's internal use.

`TmuxProcess` and `HealthProbe` do not touch `InferenceService` or `ServiceContext`. They are plain utility classes importable by any code.

---

## Tests

- `test_tmux_process.py`: unit-testable without a live tmux session — mock subprocess calls with `subprocess.run` patched.
- `test_health_probe.py`: unit-testable — mock `urllib.request.urlopen`.
- `test_background_install_session.py`: unit-testable — subclass with a trivial `_run_inner` that publishes steps and checks cancel.
- `test_service_controls.py`: integration test in `genesis_worker/tests/` — render with mocked `svc` and `status`, assert widget keys, button labels, and badge text.
- `test_tail_log.py`: integration test — render with mocked `svc.tail_log`, assert fragment registration.
