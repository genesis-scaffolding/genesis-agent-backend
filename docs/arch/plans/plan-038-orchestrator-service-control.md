# Plan: ADR-038 — orchestrator-driven service control

Implements ADR-038. Five independently committable phases. Each phase ends with the test/type/lint gate green and a runnable worker.

## Phase 1 — `_pending_orchestrator_config` on the ABC + hook handler

Foundation. No behaviour change for existing services or the API surface. Two new pieces:

1. The transient attribute on `InferenceService`.
2. The new hook kind `materialize_orchestrator_config` in `utils/services/hooks.py`.

### 1.1 Transient attribute on `InferenceService`

**`genesis_worker/contracts/service.py`** — `InferenceService.__init__`:

```python
def __init__(self, ctx: ServiceContext) -> None:
    super().__init__(ctx)
    self._ctx: ServiceContext = ctx
    # Set by the facade before start() when an orchestrator config was
    # passed; consumed by the materialize_orchestrator_config pre-start
    # hook. Cleared in the same facade call. None outside an active start.
    self._pending_orchestrator_config: dict | None = None
```

No new abstract methods. The attribute is documented as framework-private.

### 1.2 New hook kind in the registry

**`genesis_worker/utils/services/hooks.py`** — append:

```python
@register("materialize_orchestrator_config")
def _materialize_orchestrator_config(entry: dict, ctx: PreStartHookContext) -> None:
    """Write ``ctx.service._pending_orchestrator_config`` to ``entry["target"]``.

    No-op when the attribute is None (start was called without an
    orchestrator config — service uses its existing on-disk config).
    Atomic write via tmp + os.replace, mirroring ``seed_yaml_whitelist``.

    ``format`` defaults to ``json``; ``yaml`` is also supported. Unknown
    formats raise ``ValueError`` so a typo in the YAML fails loudly at
    start time rather than silently writing the wrong shape.
    """
    config = getattr(ctx.service, "_pending_orchestrator_config", None)
    if config is None:
        return
    target = Path(entry["target"])
    fmt = entry.get("format", "json")
    if fmt not in ("json", "yaml"):
        raise ValueError(
            f"materialize_orchestrator_config: unsupported format {fmt!r}; "
            "expected 'json' or 'yaml'"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    # Defensive: drop stale tmp artifacts from a prior crashed write so the
    # os.replace below doesn't trip on a half-written file.
    for stale in target.parent.glob(f"{target.name}.tmp.*"):
        try:
            stale.unlink()
        except OSError:
            pass
    tmp = target.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    if fmt == "json":
        with tmp.open("w") as f:
            json.dump(config, f, indent=2, sort_keys=False)
    else:
        with tmp.open("w") as f:
            yaml.safe_dump(config, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, target)
```

Imports added at module top: `json`, `secrets`. `yaml` is already imported transitively through `seed_yaml_whitelist`; if not, add the import.

### 1.3 Tests

**`genesis_worker/tests/test_yaml_loader.py`** — extend:

- `test_docker_with_materialize_orchestrator_config_hook_resolves_target` — declare a minimal spec with one `materialize_orchestrator_config` entry pointing at `$data_dir/config.json`; load it; assert `svc.config.pre_start_hooks[0]` has the expected resolved fields.
- `test_docker_with_unknown_format_raises_at_start` — declare a spec with `format: toml`; construct a service; set `_pending_orchestrator_config = {"x": 1}`; monkeypatch the container `run` so `start()` proceeds; assert it raises `ValueError` with the expected message.

**`genesis_worker/tests/test_declarative_specs.py`** — extend:

- `test_bifrost_pre_start_hooks_includes_materialize_orchestrator_config` — load bifrost.yaml; assert the hook list contains a `materialize_orchestrator_config` entry pointing at `<ctx.data_dir>/data/config.json`.

Phase 1 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 2 — bifrost.yaml opts in

A one-line addition to the existing bifrost spec.

### 2.1 Add the hook entry

**`genesis_worker/services/_declarative/bifrost.yaml`** — append to `pre_start_hooks`:

```yaml
pre_start_hooks:
  - kind: materialize_orchestrator_config
    target: "$data_dir/data/config.json"
```

The path substitution already works via the existing `$data_dir` placeholder (ADR-035 loader). The container's `/app/data` volume binds here; bifrost reads `config.json` from `/app/data` on container start.

### 2.2 Tests

**`genesis_worker/tests/test_declarative_specs.py`** — phase 1 already added the bifrost-specific assertion; phase 2 confirms the YAML loads cleanly with the new entry. Re-run the bifrost test group; nothing should regress.

**`genesis_worker/tests/test_bifrost_config_materialisation.py`** (new file) — hermetic end-to-end on the hook alone:

- `test_hook_writes_orchestrator_config_to_target` — load bifrost.yaml against a tmp context; set `_pending_orchestrator_config = {"providers": {"openai": {...}}}`; call the hook directly with a `PreStartHookContext(service=svc, state_dir=..., data_dir=...)`; assert the target file exists and parses as the input dict.
- `test_hook_no_op_when_pending_config_is_none` — call the hook without setting the attribute; assert no file is written.
- `test_hook_supports_yaml_format` — declare `format: yaml` on a test spec; verify the file parses as YAML and round-trips.
- `test_hook_unknown_format_raises` — set `format: xml`; assert `ValueError`.

Phase 2 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 3 — Facade methods (`install_service`, `start_service(config=)`, `restart_service`)

The facade learns three methods. Two new typed exceptions live in `contracts/service.py` (or a new module) so the route layer can translate to 409 / 503 cleanly.

### 3.1 New exception types

**`genesis_worker/contracts/service.py`** — append:

```python
class ServiceCapabilityError(Exception):
    """The service cannot perform the requested action.

    Raised by the facade when an endpoint requires a capability the
    service doesn't have (e.g. ``install`` on a ``can_install=False``
    service). The route layer translates this to ``409 Conflict``.
    """

class InstallInProgressError(Exception):
    """Another caller is mid-install for this service.

    Raised by ``install_service`` when a per-service install lock is
    held. The route layer translates this to ``503 Service Unavailable``;
    the caller should retry once the in-flight install completes.
    """
```

`KeyError` already covers the "unknown service" case (route layer → 404).

### 3.2 `install_service` on the facade

**`genesis_worker/facade.py`** — add:

```python
def install_service(self, name: str) -> dict:
    """Run the service's primary installable to completion.

    Idempotent: returns ``{"installed": True, "version": ...}`` if the
    installable is already present, with the resolved version.

    Raises:
        KeyError: ``name`` is not a registered service (route → 404).
        ServiceCapabilityError: ``can_install`` is False or
            ``installs()`` is empty (route → 409).
        InstallInProgressError: another caller is mid-install (route → 503).
    """
    svc = self._service_registry.get(name)  # KeyError → 404
    caps = svc.capabilities()
    if not caps.can_install or not svc.installs():
        raise ServiceCapabilityError(
            f"service {name!r} cannot be installed"
        )
    installable = svc.primary_installable()
    if installable is None:
        raise ServiceCapabilityError(
            f"service {name!r} has no primary installable"
        )
    if installable.state() == InstallState.INSTALLED:
        return {"installed": True, "version": installable.installed_version()}

    # Install lock: per-service, non-blocking acquire. Held across the
    # wait so a second caller gets 503 immediately rather than queueing.
    lock = self._install_lock_for(name)
    if not lock.acquire(blocking=False):
        raise InstallInProgressError(f"install already in progress for {name!r}")

    try:
        session = installable.install()
        final = session.wait()
        if final.kind == AcquireStateKind.FAILED:
            raise RuntimeError(f"install failed: {final.error or 'unknown error'}")
        if final.kind == AcquireStateKind.CANCELLED:
            raise RuntimeError("install cancelled")
        return {"installed": True, "version": installable.installed_version()}
    finally:
        lock.release()
```

The lock lives on `self._install_locks: dict[str, threading.Lock]`, lazily populated in `_install_lock_for(name)`. Process-local — sufficient under ADR-033's single-uvicorn-worker rule.

### 3.3 `start_service(name, *, config=None)` — extended

**`genesis_worker/facade.py`** — replace the existing single-arg `start_service`:

```python
def start_service(self, name: str, *, config: dict | None = None) -> StartResult:
    """Start the service, materialising ``config`` first when present.

    Not installed → delegate to ``install_service`` first.
    Running → stop, then start.
    Not running → start.

    When ``config`` is non-None, the facade sets
    ``svc._pending_orchestrator_config`` before invoking ``start()``; the
    ``materialize_orchestrator_config`` pre-start hook (if declared)
    consumes it during the start sequence. The attribute is cleared in
    a ``finally`` block so a single failed start doesn't leave state on
    the service instance.
    """
    svc = self._service_registry.get(name)  # KeyError → 404

    # Install-if-missing. install_service raises KeyError → 404,
    # ServiceCapabilityError → 409, InstallInProgressError → 503.
    if not svc.is_available():
        self.install_service(name)

    if svc.is_running():
        stop_result = svc.stop()
        if not stop_result.ok:
            return StartResult(
                ok=False,
                message=f"failed to stop before restart: {stop_result.message}",
            )

    svc._pending_orchestrator_config = config
    try:
        return svc.start()
    finally:
        svc._pending_orchestrator_config = None
```

### 3.4 `restart_service(name, *, config=None)` — new

```python
def restart_service(self, name: str, *, config: dict | None = None) -> StartResult:
    """Convenience for stop + start with optional config.

    Implemented as start_service(name, config=config). The orchestrator
    can issue stop + start separately if it wants tighter control.
    """
    return self.start_service(name, config=config)
```

### 3.5 Tests

**`genesis_worker/tests/test_facade.py`** — extend:

- `test_install_service_idempotent_when_already_installed` — `worker.install_service("bifrost")` returns `{"installed": True, ...}` without firing a real pull.
- `test_install_service_runs_primary_installable_to_completion` — monkeypatch the installable's `install()` to return a stub `DockerPullAcquireSession`-shaped object whose `wait()` returns a `complete` view; assert the facade returns the success payload.
- `test_install_service_raises_capability_error_when_can_install_false` — monkeypatch `capabilities()` to set `can_install=False`; assert `ServiceCapabilityError`.
- `test_install_service_raises_capability_error_when_installs_empty` — monkeypatch `installs()` to return `[]`; assert `ServiceCapabilityError`.
- `test_install_service_raises_in_progress_when_lock_held` — acquire the lock externally; assert `InstallInProgressError`.
- `test_install_service_raises_on_acquire_failure` — monkeypatch `install()` to return a session whose `wait()` reports `failed`; assert `RuntimeError` (route layer → 500).
- `test_start_service_with_config_sets_pending_attribute` — monkeypatch the service's `start` to capture `self._pending_orchestrator_config`; call `worker.start_service("llama_swap", config={"x": 1})`; assert the captured value matches.
- `test_start_service_clears_pending_attribute_on_completion` — same monkeypatch, but call `worker.start_service("llama_swap", config=None)`; assert the captured value is `None` (existing behaviour preserved).
- `test_start_service_clears_pending_attribute_on_failure` — monkeypatch `start` to raise; assert `_pending_orchestrator_config` is `None` after the call.
- `test_start_service_runs_install_first_when_unavailable` — monkeypatch `is_available` to return `False`; monkeypatch `install_service`; assert it was called before `start`.
- `test_start_service_stops_before_starting_when_running` — monkeypatch `is_running` to return `True`; assert `stop` was called before `start`.
- `test_restart_service_delegates_to_start_service` — assert `worker.restart_service("llama_swap", config={...})` invokes `start_service` with the same config.

Phase 3 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 4 — API schemas + deps.py install lock + 4 routes

The route layer. Pydantic schemas, install lock dict, route handlers with the right 404 / 409 / 503 / 500 mapping.

### 4.1 Install lock on the deps module

**`genesis_worker/api/deps.py`** — extend with a process-local install lock dict:

```python
import threading

# Per-service install locks. Process-local — sufficient under ADR-033's
# single-uvicorn-worker rule. The route layer holds a lock across the
# full install path so a second caller gets 503 immediately.
_install_locks: dict[str, threading.Lock] = {}

def install_lock_for(name: str) -> threading.Lock:
    """Return the per-service install lock, creating on first call."""
    lock = _install_locks.get(name)
    if lock is None:
        lock = threading.Lock()
        _install_locks[name] = lock
    return lock
```

### 4.2 New pydantic schemas

**`genesis_worker/api/schemas.py`** — append:

```python
class ConfigMaterialiseRequest(BaseModel):
    """Optional body for POST /start and /restart.

    ``config`` is forwarded verbatim to the per-service materialisation
    path. The framework does not interpret it.
    """
    config: dict[str, Any] | None = None


class InstallResponseSchema(BaseModel):
    """Body for POST /install."""
    installed: bool
    version: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> InstallResponseSchema:
        return cls(
            installed=bool(payload.get("installed")),
            version=payload.get("version"),
        )
```

`StartResult` / `StopResult` are already in `genesis_worker/contracts/service.py`. The existing `ServiceSummarySchema`-style `from_*` classmethod pattern is reused — add equivalent `StartResultSchema` / `StopResultSchema` if not already present (check the existing schema file in phase 4.0 prep; the route returns the dataclass fields directly via `response_model` and pydantic handles the coercion).

### 4.3 Four POST routes

**`genesis_worker/api/routes/services.py`** — extend. Each route follows the same shape: lookup → call facade → translate exception to HTTPException.

```python
from fastapi import APIRouter, HTTPException
from ...contracts import InstallInProgressError, ServiceCapabilityError
from ..deps import WorkerDep, install_lock_for
from ..schemas import ConfigMaterialiseRequest, InstallResponseSchema

# Existing routes (GET /services, GET /services/{name}, GET /services/{name}/status)
# stay unchanged. The four POST routes land at the bottom of the file.

@router.post("/services/{name}/install", response_model=InstallResponseSchema)
def install_service(name: str, worker: WorkerDep) -> InstallResponseSchema:
    lock = install_lock_for(name)
    if not lock.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail=f"install already in progress for {name!r}; retry shortly",
        )
    try:
        try:
            payload = worker.install_service(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown service: {name}")
        except ServiceCapabilityError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return InstallResponseSchema.from_payload(payload)
    finally:
        lock.release()


@router.post("/services/{name}/start")
def start_service(name: str, body: ConfigMaterialiseRequest | None = None,
                  worker: WorkerDep = ...) -> dict:
    try:
        result = worker.start_service(name, config=(body.config if body else None))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown service: {name}")
    except ServiceCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        # 500 — start failed after materialisation. The on-disk config
        # has been written; the service is stopped. Per the orchestrator
        # note, callers surface this and do not retry blindly.
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": result.ok, "message": result.message, "pid": result.pid}


@router.post("/services/{name}/stop")
def stop_service(name: str, worker: WorkerDep) -> dict:
    try:
        return worker.stop_service(name).__dict__
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown service: {name}")


@router.post("/services/{name}/restart")
def restart_service(name: str, body: ConfigMaterialiseRequest | None = None,
                    worker: WorkerDep = ...) -> dict:
    try:
        result = worker.restart_service(name, config=(body.config if body else None))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown service: {name}")
    except ServiceCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": result.ok, "message": result.message, "pid": result.pid}
```

The `KeyError` → 404 mapping uses FastAPI's `HTTPException`. `ServiceCapabilityError` → 409, `InstallInProgressError` → 503, `RuntimeError` from a failed start → 500 — all per the orchestrator's note.

### 4.4 Tests

**`genesis_worker/tests/test_api.py`** — extend. The existing fixture (`worker`, `client`) covers hermetic isolation. New tests stub the facade methods so no real subprocess / docker fires.

- `test_post_install_happy_path` — stub `install_service` to return `{"installed": True, "version": "v1"}`; assert 200 + body shape.
- `test_post_install_404_unknown_service` — assert 404 when the facade raises `KeyError`.
- `test_post_install_409_capability_refused` — assert 409 when the facade raises `ServiceCapabilityError`.
- `test_post_install_503_when_lock_held` — acquire the lock externally before the request; assert 503.
- `test_post_install_idempotent_re_call` — stub returns `{"installed": True, "version": "v1"}` twice; assert both calls succeed (the facade is idempotent; the route just forwards).
- `test_post_start_happy_path_with_no_body` — `worker.start_service("llama_swap")` returns `StartResult(ok=True, ...)`.
- `test_post_start_with_config_passes_through_to_facade` — body `{"config": {"x": 1}}`; assert the stubbed facade received `config={"x": 1}`.
- `test_post_start_404_unknown_service` — `KeyError` from facade → 404.
- `test_post_start_500_when_facade_returns_failed_start` — `RuntimeError("start failed after materialisation")` → 500 with the message in `detail`.
- `test_post_stop_happy_path` — stub `stop_service` to return `StopResult(ok=True, ...)`; assert 200.
- `test_post_stop_idempotent_when_not_running` — stub returns `StopResult(ok=True, message="not running")`; assert 200 (the facade handles the no-op).
- `test_post_stop_404_unknown_service` — `KeyError` → 404.
- `test_post_restart_happy_path` — stub `restart_service` to return a `StartResult`; assert 200 + body.
- `test_post_restart_with_config_passes_through` — body `{"config": {"providers": {...}}}`; assert the stubbed facade received the same config.
- `test_post_restart_500_when_facade_returns_failed_start` — `RuntimeError` → 500.

The plugin-boundary test (`test_plugin_boundary.py`) stays green: nothing in this phase imports plugin internals from framework code.

Phase 4 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 5 — Tutorial update + final gate

### 5.1 Tutorial update

**`docs/tutorials/declarative-services.md`** — add a section under "Pre-start hooks" documenting the new kind:

```markdown
### `materialize_orchestrator_config`

Writes the orchestrator-supplied config object (passed to
`POST /v1/services/{name}/start` or `/restart`) to the declared target
before the container starts. The hook is a no-op when no config was
provided — services without a config body start with their on-disk
state, matching today's behaviour.

```yaml
pre_start_hooks:
  - kind: materialize_orchestrator_config
    target: "$data_dir/data/config.json"
```

The `format` field defaults to `json`; `yaml` is also supported. Unknown
formats raise `ValueError` at start time. The atomic-write guarantee
mirrors `seed_yaml_whitelist`: tmp file + `os.replace`.

`$data_dir` resolves to the per-service `<ctx.data_dir>`; bind-mount
this target into the container (e.g. `volumes: { /app/data: "$data_dir/data" }`)
so the service reads the freshly-written config on boot.
```

### 5.2 Cross-references

- ADR-038 — link from ADR-033's deferred-items section: the "write endpoints" line is removed and replaced with a "Implemented by ADR-038" pointer. Inline annotation; no status change.
- `docs/notes/orchestrator-service-control.md` — link the orchestrator's note from ADR-038's Status section. Already done in the ADR text.

### 5.3 Final gate

After phase 5:

```bash
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

All four must pass. End-to-end smoke: launch the worker, hit each of the four new endpoints with a stub service registered as `bifrost`, confirm the right status codes for happy / 404 / 409 / 503 / 500 paths.

---

## Out of scope for this change (deferred)

- Auth / authorization — same posture as ADR-033. Tailscale is the gate.
- Multi-orchestrator coordination / HA — v1 assumes one orchestrator per worker.
- Bulk endpoints (`POST /v1/services:bulk`) — out of scope per the orchestrator note.
- Webhook / event push for state changes — orchestrator continues to poll.
- Cancellation of in-flight install or start operations.
- Multi-worker uvicorn. The install lock is process-local; ADR-033 caps uvicorn at one worker for v1.
