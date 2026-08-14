# ADR-013: Framework-provided service lifecycle utilities

## Title

Framework-provided service lifecycle utilities.

## Status

Proposed.

## Context

Both implemented services — `llama_swap` and `cptr` — follow the same patterns at every layer, despite entirely different install backends:

| Layer | llama-swap | cptr | Difference |
|-------|-----------|------|------------|
| `lifecycle.py` | `start_swap()` / `stop_swap()` | `start_cptr()` / `stop_cptr()` | Only the binary invocation args |
| Install session | `_GithubReleaseInstallSession` | `_UvToolInstallSession` | Only `_run_inner()` |
| Status page | `ui/status.py` | `ui/status.py` | Same structure; llama-swap adds variant selector |
| `uninstall_installable()` | Same guard logic | Same guard logic | Identical |
| `tail_log()` | Same seek + decode | Same seek + decode | Identical |
| `public_host()` | Same fallback chain | Same fallback chain | Identical |

The tmux plumbing in `lifecycle.py` is the clearest example: both files contain ~100 lines, of which ~85 are copy-paste-identical. The llama-swap version adds a child-drain loop for `llama-server` subprocesses; the cptr version omits it (cptr has no children). Even so, the base session management (`_has_session`, `kill_session`, `send_keys C-c`) is identical.

The plugin boundary (ADR-009) is not violated — utilities in `genesis_worker/utils/` are a leaf package that may import from `contracts/`. Plugins call the utility; the utility does not call into plugin internals.

## Decision

We will provide four framework utilities. Plugins and the framework dashboard import from `genesis_worker/utils/`; they no longer reimplement these patterns.

### 1. `genesis_worker/utils/process/tmux.py` — `TmuxProcess`

Stateless tmux session management. Replaces the identical `_has_session`, `is_running`, `status`, and the start/stop scaffolding from both `lifecycle.py` files.

```python
class TmuxProcess:
    def __init__(self, session_name: str) -> None: ...

    def exists(self) -> bool: ...
    def kill(self) -> None: ...
    def send_interrupt(self) -> None: ...   # sends C-c to the foreground process group

    def start(
        self,
        cmd: str,
        log_file: Path,
        *,
        wait_for_children: Callable[[], bool] | None = None,
        child_wait_timeout_s: float = 30.0,
    ) -> StartResult: ...

    def stop(self) -> StopResult: ...

    @staticmethod
    def has_session(name: str) -> bool: ...
```

- `start()` writes `cmd 2>&1 | tee -a <log_file>` internally, so callers pass only the binary and args.
- `wait_for_children` is an optional predicate; llama-swap passes `_no_llama_servers`; cptr passes `None`.
- `_has_session` is renamed to the more conventional `exists()` and lifted to a static method for targeted use without a class instance.

### 2. `genesis_worker/utils/net/probe.py` — HealthProbe

HTTP readiness polling and status building. Replaces the identical `wait_ready`, `_probe_models`, `_probe_root`, `_probe_host`, and the `status()` function bodies from both `lifecycle.py` files.

```python
class HealthProbe:
    def __init__(self, host: str, port: int, *, probe_path: str = "/v1/models") -> None: ...
    """probe_path defaults to the OpenAI /v1/models endpoint.
    Set to '/' for services with no OpenAI-compatible API."""

    def probe(self) -> bool: ...          # single synchronous probe; returns True on 200
    def wait_ready(self, timeout_s: float) -> bool: ...

    @staticmethod
    def resolve_connect_host(host: str) -> str:  # 0.0.0.0/:: → 127.0.0.1
```

The `status()` function in each `lifecycle.py` is reduced to:

```python
def status(session_name: str, probe: HealthProbe) -> ServiceStatus:
    endpoint = f"http://{probe._host}:{probe._port}{probe._path.rsplit('/', 1)[0]}/"
    if not TmuxProcess(session_name).exists():
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    if probe.probe():
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)
```

### 3. `genesis_worker/utils/install/session.py` — `BackgroundInstallSession`

Base class for streaming install sessions. Replaces the identical `BackgroundInstallSession`, `_SessionState`, `current_step`, `submit`, `cancel`, `wait`, `_publish`, `_run`, and the thread-start boilerplate from both `install.py` files. Each concrete session implements only `_run_inner`.

```python
class BackgroundInstallSession(InstallSession):
    """Streaming install session with a daemon worker thread.

    Subclasses implement ``_run_inner()`` to perform the actual install
    (download, uv install, etc.). The thread handles cancellation via
    ``self._cancel: threading.Event``.
    """

    def __init__(self) -> None:
        self._state = _SessionState(
            step=AcquireStep(kind="fetching", title=f"installing {self._name}")
        )
        self._cancel = threading.Event()
        self._done = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    @abstractmethod
    def _name(self) -> str: ...

    @abstractmethod
    def _run_inner(self) -> None:
        """Perform the install. Check ``self._cancel.is_set()`` between steps.

        On success, call ``self._publish(AcquireStep(kind='complete', ...))``.
        On failure, raise an exception (it is caught and turned into a 'failed' step).
        To signal cancellation, raise ``_Canceled``.
        """
        ...

    def current_step(self) -> AcquireStep: ...
    def submit(self, choice: AcquireChoice) -> AcquireStep: ...
    def cancel(self) -> None: ...
    def wait(self) -> AcquireStep: ...
```

`InstallSession` in `contracts/install.py` gains no new methods. `BackgroundInstallSession` is not a contract — it is a concrete utility in `utils/`. Plugins subclass it; they do not implement `InstallSession` directly.

The `_Canceled` sentinel is private to `utils/install/session.py`.

### 4. `genesis_worker/utils/ui/_service_controls.py` — `render_service_controls()`

Streamlit component for the service info + start/stop block on each status page. Replaces the copy-paste service-info section from both `ui/status.py` files.

```python
def render_service_controls(
    svc: InferenceService,
    status: ServiceStatus,
    *,
    show_web_ui_link: bool = True,
    key_prefix: str = "",
) -> None:
    """Render badge, Start/Stop button, inline install, and Web UI link.

    The ``key_prefix`` namespaces widget keys so multiple instances on
    the same page don't collide.
    """
```

The llama-swap status page passes `key_prefix="status-llama_swap"`; cptr passes `key_prefix="status-cptr"`.

### 5. `genesis_worker/utils/ui/_tail_log.py` — `render_tail_log()`

Streamlit fragment for the live console tail. Replaces the copy-paste `@st.fragment(run_every="2s")` + `tail_log()` block from both `ui/status.py` files.

```python
def render_tail_log(
    svc: InferenceService,
    *,
    n_bytes: int = 8192,
    key: str = "",
) -> None:
    """Render an auto-refreshing console tail for ``svc``'s log file.

    ``key`` namespaces the fragment so multiple instances on the same
    page don't conflict.
    """
```

Both `InferenceService` instances already expose `tail_log(n_bytes)`, so `render_tail_log` calls it through the contract interface.

## Consequences

**Positive**

- A new service plugin needs only: implement the ABC, subclass `BackgroundInstallSession`, wire in `TmuxProcess` and `HealthProbe` — no reimplementation of tmux orchestration or log tailing.
- The tmux + child-drain logic is in one tested place. The llama-swap child-drain feature was discovered in production; having it in a shared module makes it easier to apply to all services.
- The status page for both services is reduced to: `render_service_controls()`, the service-specific section, and `render_tail_log()`.
- `TmuxProcess` being stateless makes it trivially testable: no fixture setup.

**Negative**

- `TmuxProcess.start()` always wraps the command in `cmd 2>&1 | tee -a log_file`. If a future service needs different I/O routing, the abstraction leaks. This is acceptable: all existing services use the same pattern; we revisit if a counterexample emerges.
- `BackgroundInstallSession` adds a thread to the install process. The thread is unavoidable given the state-machine shape of `InstallSession`, but it means `cancel()` cannot synchronously drain — it sets an event and returns. The cancellation contract is unchanged.
- `render_service_controls()` takes an `InferenceService` argument. This is a framework import (contracts may be imported by utils), not a plugin boundary violation, but it is worth noting that the UI utility depends on the service contract.

**Neutral**

- The utilities live in `utils/`, which is a leaf package. The plugin boundary test (`test_plugin_boundary.py`) walks plugin directories and flags illegal imports — `genesis_worker.utils` is not in scope, so utilities may import from `contracts/`.
- `lifecycle.py` files in both services shrink to ~20 lines each. They become thin wrappers that compose the framework utilities.
- `install.py` files in both services shrink: the session class is replaced by a subclass of `BackgroundInstallSession`, leaving only `_run_inner` and the `ServiceInstall` adapter methods.

## Spec

[docs/arch/specs/spec-008-service-lifecycle-utilities.md](../specs/spec-008-service-lifecycle-utilities.md)

## Plan

[docs/arch/plans/plan-008-service-lifecycle-utilities.md](../plans/plan-008-service-lifecycle-utilities.md)
