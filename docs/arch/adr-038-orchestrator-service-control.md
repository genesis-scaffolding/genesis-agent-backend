# ADR-038: Orchestrator-driven service control — write endpoints on the FastAPI surface

## Title

Orchestrator-driven service control — four `POST /v1/services/{name}/{action}` endpoints on the existing FastAPI process, plus an orchestrator-config materialisation path that flows through the existing `pre_start_hooks` mechanism.

## Status

Accepted. Supersedes ADR-033's "Write endpoints (start/stop services, drive sessions, regenerate config): deferred" entry.

## Context

The Genesis orchestrator today reads from each worker over the FastAPI surface (`/v1/services`, `/v1/host`, `/v1/metrics`, `/v1/paths`) but has no path to install, start, stop, or configure a service on a worker. Every write the orchestrator performs goes through Ansible + SSH (source deploy, lifecycle start/stop of the worker tmux session). The orchestrator's stated goal is to drive a **bifrost LLM gateway** on a designated worker: install it, start it with a `config.json` the orchestrator composes from the rest of the fleet's llama-swap endpoints, and refresh / restart it as the fleet changes. The same endpoints are general — any docker or uv-tool declarative service is a candidate for orchestrator control.

This change introduces four `POST /v1/services/{name}/{action}` endpoints on the existing FastAPI process (no new port, no separate control surface):

- `POST /v1/services/{name}/install` — install the primary installable if missing; idempotent.
- `POST /v1/services/{name}/start` — start the service, optionally materialising a config object first.
- `POST /v1/services/{name}/stop` — stop the service if running; idempotent.
- `POST /v1/services/{name}/restart` — convenience for stop + start with optional config.

The orchestrator's contract for these endpoints is captured in `docs/notes/orchestrator-service-control.md`. We accept it as written; nothing here re-opens the orchestrator-side design.

### Forces

- **Tailscale is the gate.** ADR-033 deliberately deferred auth — the API sits behind Tailscale, no application-level auth. v1 of the orchestrator endpoints inherits the same posture. Bearer-token auth, when it lands, applies uniformly to read and write endpoints; that's a separate ADR.
- **Framework/plugin boundary (ADR-009).** The new endpoints are framework code (`genesis_worker/api/`). The plugin boundary test stays clean: no plugin reaches behind its facade. Materialisation logic, however, must be expressible from a YAML spec — bifrost is currently YAML (`services/_declarative/bifrost.yaml`), and we don't want to convert it to Python just to add a hook.
- **Capability-gated behaviour.** The endpoints gate on what the service can actually do: `can_install=False` for install, `installs()` empty for install, etc. The existing `ServiceCapabilities` and `installs()` patterns are the right precedent.
- **Config object is service-defined.** The orchestrator passes through whatever it built; the worker dispatches on `{name}` to decide where to write it. The framework doesn't interpret the config object — only validates the request shape and forwards the blob to the per-service materialisation path.
- **Materialisation happens before start.** The orchestrator's note: "Materialisation happens before stop so the freshly-started process picks up the new config on next launch." With hook-driven materialisation the file lands on disk during `start()`, after `stop()`. The freshly-started container still reads the new file on boot, which is the actual requirement; "before stop" is the user's mental model. We capture this in the plan as a clarification, not a deviation.
- **`pre_start_hooks` is the right vehicle.** The existing hook registry (`genesis_worker/utils/services/hooks.py`) already supports idempotent file writes with the right atomic-write pattern (`tmp` + `os.replace`). Adding a new hook kind is one ~15-line function + a `@register("…")` decorator. bifrost.yaml stays YAML.
- **Per-request input.** `pre_start_hooks` today is stateless — handlers read filesystem state and write filesystem state. The orchestrator's config flows in at HTTP-request time, not at YAML-construction time. We need a thin mechanism to thread the config into the hook handler at start time.
- **Existing contract stays untouched.** `InferenceService.start()` is parameterless; every override (`LlamaSwapService.start`, `ComfyuiService.start`, `DockerService.start`, `UvService.start`) has no kwargs. We must not change the signature — that would touch every plugin without adding value.

## Decision

### 1. Four `POST` endpoints on the existing `genesis_worker/api/routes/services.py`

All endpoints accept JSON; only `start` and `restart` accept a body. Error responses use FastAPI's default `{"detail": "..."}` shape. The route file is framework code; the plugin boundary test stays green.

| Endpoint | Body | 200 shape | 404 | 409 | 500 | 503 |
|---|---|---|---|---|---|---|
| `POST /v1/services/{name}/install` | empty | `{"installed": true, "version": "..."}` | unknown service | `can_install=False` or `installs()` empty | — | install already in progress |
| `POST /v1/services/{name}/start` | optional `{"config": {...}}` | `StartResult` (`{ok, message, pid}`) | unknown service | not startable in current state (e.g. binary missing for a non-install path) | start failed after materialisation | — |
| `POST /v1/services/{name}/stop` | empty | `StopResult` (`{ok, message}`) | unknown service | — | — | — |
| `POST /v1/services/{name}/restart` | optional `{"config": {...}}` | `StartResult` | unknown service | as `start` | as `start` | — |

The full request/response contract is in `docs/notes/orchestrator-service-control.md`. The route layer translates the orchestrator's expectations into facade calls; the facade translates those into plugin calls; the plugin boundary holds.

### 2. Facade extensions — install, restart, and `start(name, config)`

Three new / extended methods on `GenesisWorker`. All keep the existing single-process semantics from ADR-033.

```python
class GenesisWorker:
    def install_service(self, name: str) -> InstallResponse:
        """Run the service's primary installable to completion.

        Idempotent. Gates on capabilities().can_install and installs();
        raises :class:`ServiceCapabilityError` for either refusal, which
        the route layer translates to 409. Raises
        :class:`InstallInProgressError` if another caller is mid-install
        for the same service — route layer translates to 503.
        """
        ...

    def start_service(self, name: str, *, config: dict | None = None) -> StartResult:
        """Start the service, materialising ``config`` first when present.

        Not installed → delegates to ``install_service()`` first. Running →
        stops first, then starts (caller can use ``restart_service()`` to
        combine both). Materialisation runs before stop via the
        ``pre_start_hooks`` mechanism; see decision 3.
        """
        ...

    def stop_service(self, name: str) -> StopResult:
        # Existing method, unchanged signature.
        ...

    def restart_service(self, name: str, *, config: dict | None = None) -> StartResult:
        """Atomic stop + start with optional config.

        Implemented as stop-then-start with a single ``_pending_orchestrator_config``
        attribute set on the service before either call. The file lands on
        disk during the start hook — the freshly-started container picks
        it up on boot, which is the actual requirement.
        """
        ...
```

The existing `start_service(self, name)` signature is replaced: a new keyword-only `config` parameter is added. Every existing call site (Streamlit UI, CLI, configure panel) calls without the keyword — Python's keyword-only semantics keep all existing callers source-compatible.

### 3. Hook-driven materialisation — `materialize_orchestrator_config`

A new hook kind in `genesis_worker/utils/services/hooks.py`. The hook reads the pending config from the service instance (set by the facade) and writes it atomically to the declared target.

```python
@register("materialize_orchestrator_config")
def _materialize_orchestrator_config(entry, ctx):
    config = getattr(ctx.service, "_pending_orchestrator_config", None)
    if config is None:
        return  # No body — service uses its on-disk config; today's behaviour.
    target = Path(entry["target"])
    fmt = entry.get("format", "json")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    if fmt == "json":
        with tmp.open("w") as f:
            json.dump(config, f, indent=2, sort_keys=False)
    elif fmt == "yaml":
        with tmp.open("w") as f:
            yaml.safe_dump(config, f, sort_keys=False)
    else:
        raise ValueError(f"unsupported format {fmt!r}; expected 'json' or 'yaml'")
    os.replace(tmp, target)
```

The hook reuses the same atomic-write pattern as `seed_yaml_whitelist` (tmp file + `os.replace`). No new utilities.

**Why a hook, not a contract method.** Three alternatives were considered:

- A `materialize_config(self, config)` abstract method on `InferenceService`. Adds an abstract method every plugin has to think about; forces bifrost to become a Python subclass to implement it.
- A top-level `orchestrator_config:` field on the YAML spec plus framework code in `DockerService.start`. New spec field plus new code path; bifrost can't share it cleanly with other services.
- The hook kind we picked. One ~15-line handler, one entry in bifrost.yaml, one transient attribute on the ABC. bifrost stays YAML. Reuses the established idempotent-write pattern. Any future YAML service opts in by adding one line.

### 4. `_pending_orchestrator_config` — transient instance attribute on the ABC

The facade sets this attribute on the service before invoking `start()`; the hook reads it via `ctx.service`. The attribute is always set+cleared within a single facade call.

```python
class InferenceService(Plugin):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._ctx = ctx
        # Set by the facade before start() when an orchestrator config
        # was passed; consumed by the materialize_orchestrator_config
        # pre-start hook. Cleared in the same facade call.
        self._pending_orchestrator_config: dict | None = None
```

The leading underscore signals "framework-private; do not touch from plugins." The hook handler is in `utils/services/hooks.py` — framework code that reads its own private state on the service. Plugin code never reaches this attribute.

### 5. bifrost.yaml opts in with one entry

```yaml
pre_start_hooks:
  - kind: materialize_orchestrator_config
    target: "$data_dir/data/config.json"
```

The `$data_dir/data/` substitution already works (ADR-035 loader). The container's `/app/data` volume (declared in the same YAML) binds to this path; bifrost reads `config.json` from `/app/data` on container start. The orchestrator's note specifies the bifrost config shape, which bifrost upstream documents — the worker just writes the dict verbatim as JSON.

### 6. Per-service install lock for 503 handling

A process-local `dict[str, threading.Lock]` in `genesis_worker/api/deps.py`. FastAPI runs sync route handlers in a threadpool (default), so a `threading.Lock` is the right primitive. The lock is acquired non-blocking in the route; failure raises `InstallInProgressError`, which the route translates to `503`. Single-uvicorn-worker mode (ADR-033) means the lock is process-local — sufficient for v1.

The lock is held only across the wait for the `AcquireSession` to reach a terminal state. Other state changes (start, stop, config refresh) proceed concurrently for the same service. Two installs of *different* services proceed concurrently.

### 7. Capability gating — `can_install` and `installs()` only

The install endpoint gates on `capabilities().can_install` and the service's `installs()` returning a non-empty list. The orchestrator's note is explicit:

> `409` — service has `can_install=False`, or its `installs()` list is empty (no primary installable).

No new capability flag is added to `ServiceCapabilities`. The "can this service accept orchestrator config?" question is answered by the presence of a `materialize_orchestrator_config` hook entry in the YAML — services without one return silently to "no orchestrator config" semantics. This is the same pattern as today's "no `pre_start_hooks` means no hooks run."

### 8. No contract change, no new abstract methods, no Python subclass for bifrost

- `InferenceService.start()` stays parameterless. All existing overrides are unaffected.
- `InferenceService` gains no new abstract methods.
- `services/_declarative/bifrost.yaml` stays YAML. No `services/bifrost/` Python package is introduced.
- The plugin boundary test (`test_plugin_boundary.py`) is unchanged — it walks plugin files only, and the new materialisation logic lives in `utils/services/hooks.py`, which is framework code.

## Plan

`docs/arch/plans/plan-038-orchestrator-service-control.md` — five phases, each independently committable, ending with the full test/type/lint gate.

## Consequences

**Positive**

- The orchestrator can install, start, stop, and restart any docker or uv-tool declarative service on a worker over the existing FastAPI surface. Bifrost and the future fleet become orchestrator-driven without per-service bespoke paths.
- The hook-driven materialisation reuses an existing mechanism. bifrost.yaml stays the single source of truth for bifrost config; no Python service class is needed for the materialisation alone.
- The facade's `start_service(name, config=None)` adds a keyword-only parameter, leaving every existing call site source-compatible (UI, CLI, configure panel).
- Tailscale continues to be the gate. No new auth surface; the v1 posture matches ADR-033. When bearer-token auth lands later, it covers the new endpoints uniformly.
- The plugin boundary test stays green. New framework code lives in `api/`, `utils/services/hooks.py`, and `facade.py`; no plugin file imports anything new.
- The 409 / 503 / 404 error mapping lives in the route layer. The facade raises typed errors (`ServiceCapabilityError`, `InstallInProgressError`, `KeyError`); the route translates each to the right HTTP status with the right body.

**Negative**

- A transient `_pending_orchestrator_config` attribute lives on `InferenceService`. It's set+cleared within one facade call, but the leading-underscore private-API-across-module-boundaries pattern is mildly smelly. Acceptable because the access is framework-to-framework (hooks.py is framework code, not plugin code); the attribute is always None outside an active start call.
- The orchestrator's "materialisation happens before stop" timing isn't literal: the file lands during the start hook (after stop). The freshly-started container still reads the new config on boot, which is the actual functional requirement. The plan documents this as a clarification, not a deviation. If a future requirement demands the file be on disk before stop (e.g. for a manual inspection window), the facade can call the write logic eagerly before invoking stop — easy add, deferred.
- The install lock is process-local. ADR-033 caps uvicorn at one worker for v1, so the lock is sufficient. A multi-worker deployment would need either a filesystem lock or an external coordinator; deferred.
- The route layer gains typed error translations (404 / 409 / 503). Three new exception types (`ServiceCapabilityError`, `InstallInProgressError`, plus reusing `KeyError`) make the facade's contract richer. Tests pin each translation.
- bifrost.yaml grows by one hook entry. The diff is small and self-contained; the rest of the spec is unchanged.

**Neutral**

- ADR-033's deferred-items list loses the "write endpoints" line; this ADR supersedes that one bullet. Other deferred items (auth, CORS, pagination, WebSocket) stay deferred.
- The orchestrator's note is now the canonical contract for these endpoints; this ADR doesn't restate it. Future readers should start at `docs/notes/orchestrator-service-control.md`.
- `bifrost.yaml`'s pre-start hooks grow from zero to one entry. The atomic-write guarantee and idempotency story are unchanged.
- `docs/tutorials/declarative-services.md` gains a section on the new hook kind, parallel to the existing `seed_yaml_whitelist` and `ensure_persistent_token` documentation.
