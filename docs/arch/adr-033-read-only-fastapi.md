# ADR-033: Read-only FastAPI surface for genesis_worker

## Title

Read-only FastAPI surface for genesis_worker — a peer process to the Streamlit UI.

## Context

The Streamlit UI is the only HTTP surface the worker exposes today.
Browser is required; programmatic access (curl, scripts, a second
backend, an external dashboard) means scraping a Streamlit server, which
fragile and unsupported.

We need a stable, documented HTTP surface for "what is this worker
doing right now" — host identity, live metrics, registered sources and
services, the current catalog, and any in-flight acquire sessions.
Every value is already accessible through `GenesisWorker`; nothing on
the worker side has to be invented.

Three structural facts shape the design:

1. **Streamlit owns its own worker instance.** The `get_worker()` cache
   lives inside the Streamlit process. There is no cross-process
   worker daemon. Any HTTP surface that consumes the facade therefore
   needs its own worker construction.
2. **Acquire sessions are per-process.** `GenesisWorker._sessions` is
   a dict held by reference; the session object itself is a Python
   object on the heap of one process. The HTTP surface cannot start a
   session in another process and poll it from this one. It can only
   view sessions started by the same process. That's fine for v1:
   read-only, and the typical pattern is one process per surface.
3. **The framework/plugin boundary (ADR-009) is strict and tested.**
   A new framework consumer — like the existing `ui/` and `cli/` —
   imports from `genesis_worker` (the facade), `genesis_worker.contracts`,
   and `genesis_worker.utils`. Plugins do not import from `api/`. We
   should not extend the facade or contracts with HTTP-shaped types;
   the API has its own pydantic schemas.

## Decision

### A FastAPI package inside `genesis_worker`

```
genesis_worker/
  api/                 peer of ui/ and cli/
    app.py             create_app() factory; lifespan constructs the worker
    deps.py            module-level GenesisWorker singleton + Depends()
    schemas.py         pydantic response models (the API's view types)
    routes/
      health.py        GET /health
      host.py          GET /v1/host
      metrics.py       GET /v1/metrics
      paths.py         GET /v1/paths
      sources.py       GET /v1/sources, GET /v1/sources/{name}
      services.py      GET /v1/services, .../{name}, .../{name}/status
      catalog.py       GET /v1/catalog, /by-source, /{source}/{name}
      sessions.py      GET /v1/sessions, GET /v1/sessions/{id}
  cli/
    api.py             genesis-worker-api console script
```

The API is a **framework consumer**, not a plugin axis. It imports
`GenesisWorker` and the contract types, and that's it. It does not
become a new place plugins have to know about.

### v1 is read-only

Every endpoint is `GET`. Writes (start/stop services, delete models,
drive acquire sessions, regenerate config) are deferred. The
read-only posture minimises blast radius — this server, behind
nothing but Tailscale, can be poked freely without auth or audit.
Adding writes later is additive: new endpoints, no breaking changes
to existing ones.

### Two-process model

Streamlit and FastAPI each construct their own `GenesisWorker`. The
on-disk state (`state_dir/catalog.json`, `state_dir/enabled_services.yaml`,
log files, install manifests) is the only shared state. There is no
cross-process IPC, no shared memory, no supervisor.

This matches today's behaviour — every consumer that already exists
(CLI commands, Streamlit, the test suite) constructs its own worker.
The API is one more such consumer.

**Acknowledged race:** two workers can race on `enabled_services.yaml`
writes if both are modifying services concurrently. The same race
exists today between Streamlit and any CLI invocation; v1 doesn't
make it worse. A supervisor process would solve it but is out of scope.

### Uvicorn, single worker

`uvicorn genesis_worker.api.app:app` with default settings — one Python
process. `--workers > 1` would mean `N` independent workers each with
its own `GenesisWorker` and its own session registry, which is a
correctness bug for anyone expecting shared sessions. Defer multi-worker
until we have an external session backend.

### No auth, no CORS

Tailscale is the network gate. v1 ships with FastAPI's default
behaviour on both fronts: no CORS headers, no authentication. Browser
callers on a different origin will hit the same-origin policy; this
is acceptable when the only callers are local curl, Tailscale-side
scripts, and same-origin dashboards. Adding auth and CORS is additive.

### Port 9090, /v1/ versioning, default bind 0.0.0.0

Defaults:

- Host: `0.0.0.0` (same as Streamlit)
- Port: `9090` (avoids the llama-swap default `8080` and Streamlit `8501`)
- Path prefix: `/v1/` for all versioned endpoints; `/health` for liveness

Both host and port are env-overridable (`GENESIS_API_HOST`,
`GENESIS_API_PORT`), matching the existing `GENESIS_UI_PORT` pattern.

### Worker construction

A module-level `GenesisWorker` singleton held by `genesis_worker.api.deps`.
The `app.py` lifespan calls `get_worker()` once at startup so plugin
construction errors surface eagerly rather than on the first request.
The Streamlit side uses `@st.cache_resource`; the API side has no
equivalent. Module-level is fine because uvicorn's default is a single
worker process.

### New console script

`genesis-worker-api = "genesis_worker.cli.api:main"` in `pyproject.toml`.
Same shape as `genesis-worker-ui`. Internally calls `uvicorn.run(...)`
rather than `subprocess.call` (uvicorn is designed to be imported;
streamlit is not).

### Makefile

- `make api` runs the API alone.
- `make serve` runs both UI and API in parallel, with a `kill 0`
  trap so Ctrl+C cleans up both.

No new dependency; `make` is the orchestrator.

## Status

Accepted.

## Consequences

Positive:
- Programmatic access to worker state without depending on Streamlit internals.
- The boundary stays clean: the API is a peer of `ui/` and `cli/`, not a new plugin axis.
- OpenAPI schema generation comes for free from FastAPI — every route is documented automatically.
- Adding writes later is additive; the read surface is the stable part.
- Tests use FastAPI's `TestClient`, which is the standard pattern and exercises the same routing code as production.

Negative:
- Two `GenesisWorker` processes can race on `enabled_services.yaml` writes. Mitigated by "users don't toggle services concurrently across both surfaces in practice"; not solved.
- The API has its own session view, separate from the Streamlit process's sessions. If a session is started in one process, the other process can't see it. Read-only posture means this only matters for `GET /v1/sessions`, which can return `[]` even when Streamlit has active sessions. Documented; not solved.
- Pydantic response models duplicate field names from contract types. Drift risk if contract types change shape; mitigated by tests.
- Adding `fastapi` + `uvicorn` to runtime dependencies increases install size for users who only run the CLI. Acceptable; both are small.

Neutral:
- No auth, no CORS, no pagination, no WebSocket. These are deferred, not rejected.
- `/health` returns `{status: "ok"}` and no version. A version field would mean pulling `pyproject.toml` metadata into the runtime; not worth the cost.

## Plan

`docs/arch/plans/plan-033-read-only-fastapi.md`.

## Out of scope (deferred)

- Write endpoints.
- Auth (bearer token or otherwise).
- CORS.
- Pagination / filtering on `/v1/catalog`.
- WebSocket / SSE for live status.
- Multi-worker uvicorn.
- Published OpenAPI docs UI.