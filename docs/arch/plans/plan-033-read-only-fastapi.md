# Plan 033: Read-only FastAPI surface for genesis_worker

Companion to ADR-033.

## Goal

Expose the genesis_worker state over HTTP as a read-only FastAPI surface
that runs as a separate process, peer of the Streamlit UI. Each process
holds its own `GenesisWorker`; no IPC; on-disk state is the only shared
state.

## Decisions locked in by ADR-033

- Read-only v1.
- Two-process model: Streamlit and FastAPI each construct their own worker.
- No auth (Tailscale is the gate).
- Port `9090`, bind `0.0.0.0`, env-overridable via `GENESIS_API_PORT` / `GENESIS_API_HOST`.
- REST polling only (no WebSocket / SSE).
- No pagination in v1.
- Path-based versioning under `/v1/`.

## File-by-file

### New code

| Path | Purpose |
|---|---|
| `genesis_worker/api/__init__.py` | Package marker. |
| `genesis_worker/api/schemas.py` | Pydantic v2 response models. One model per resource; `from_*` classmethods that convert from the contract dataclasses and facade view types. |
| `genesis_worker/api/deps.py` | Module-level `GenesisWorker` singleton + `get_worker()` FastAPI dependency. Constructed lazily on first call. |
| `genesis_worker/api/app.py` | `create_app()` factory; lifespan that eagerly constructs the worker so plugin errors surface at startup; mounts every router under `/v1` (health is unversioned at `/health`). |
| `genesis_worker/api/routes/health.py` | `GET /health` → `{status: "ok"}`. |
| `genesis_worker/api/routes/host.py` | `GET /v1/host`. |
| `genesis_worker/api/routes/metrics.py` | `GET /v1/metrics`. |
| `genesis_worker/api/routes/paths.py` | `GET /v1/paths` — the resolved path settings (vault, data, config, cache, state, log, repo_root). |
| `genesis_worker/api/routes/sources.py` | `GET /v1/sources`, `GET /v1/sources/{name}`. |
| `genesis_worker/api/routes/services.py` | `GET /v1/services`, `GET /v1/services/{name}`, `GET /v1/services/{name}/status`. |
| `genesis_worker/api/routes/catalog.py` | `GET /v1/catalog`, `GET /v1/catalog/by-source`, `GET /v1/catalog/{source}/{name}`. |
| `genesis_worker/api/routes/sessions.py` | `GET /v1/sessions`, `GET /v1/sessions/{session_id}`. |
| `genesis_worker/cli/api.py` | `genesis-worker-api` console script. Uses `uvicorn.run` against `genesis_worker.api.app:app`. Reads `GENESIS_API_HOST` / `GENESIS_API_PORT`. |
| `genesis_worker/tests/test_api.py` | FastAPI `TestClient` tests. Uses a fixture-built `GenesisWorker` with isolated `state_dir`; never touches real subprocesses or the user's `~/.local/state/genesis-worker/`. |

### Edited

- `pyproject.toml` — runtime deps via `uv add fastapi uvicorn`; `[project.scripts]` entry `genesis-worker-api = "genesis_worker.cli.api:main"` (this is not a dependency, so the `uv add` path doesn't apply; this is a packaging edit, manual).
- `Makefile` — new `api` and `serve` targets; `help` updated.

### Docs

- `docs/arch/adr-033-read-only-fastapi.md` — pins the decisions.
- This file (linked from the ADR's **Plan** section).

## API surface

```
GET /health
GET /v1/host
GET /v1/metrics
GET /v1/paths
GET /v1/sources
GET /v1/sources/{name}
GET /v1/services
GET /v1/services/{name}
GET /v1/services/{name}/status
GET /v1/catalog
GET /v1/catalog/by-source
GET /v1/catalog/{source}/{name}
GET /v1/sessions
GET /v1/sessions/{session_id}
```

`GET /v1/services/{name}` includes the full `ServiceStatus` plus
`runtime_endpoint` / `web_ui_endpoint` (None when not running). The
`/status` subroute returns the cheap status-only payload for clients
that poll frequently.

## Implementation notes

- **Worker construction** — `deps.get_worker()` returns a module-level singleton. The `app.py` lifespan calls it once at startup so plugin construction errors surface eagerly rather than on the first request. The Streamlit side uses `@st.cache_resource`; we have no equivalent. Module-level is the right pattern for `uvicorn` workers (`--workers 1` only — see ADR).
- **Session lookup** — `GET /v1/sessions/{id}` iterates `worker.list_acquire_sessions()` and matches by id. We don't add a `get_acquire_session(id)` method to the facade; iteration is fine for the rare lookup, and `list_acquire_sessions` already gives us the id+session pair. `list_acquire_sessions` has a side effect (drops terminal sessions), but that's consistent with the facade contract.
- **404s** — single source/service/catalog-entry/session lookups return 404 when not found. `/v1/sources` returning an empty list is not a 404 — the source is registered, the list is empty.
- **Path serialization** — schemas use `str` for filesystem paths, not `pathlib.Path`. Conversion happens in the `from_*` classmethods. Pydantic v2 doesn't auto-coerce `Path` to str.
- **CORS** — none. Consumers are local or Tailscale-side. Browser callers on a different origin will hit the same-origin policy; this is acceptable for v1.
- **Tests** — `TestClient(app)` with `with` block so the lifespan fires. Tests use `Settings(paths=PathsSettings(state_dir=tmp_path / "state"))` to hermeticise.

## Makefile

```makefile
api:
\tuv run genesis-worker-api

serve:
\t@echo "Starting UI on $${GENESIS_UI_PORT:-8501} and API on $${GENESIS_API_PORT:-9090}..."
\t@trap 'kill 0' EXIT INT TERM; \\
\tuv run genesis-worker-ui & \\
\tuv run genesis-worker-api & \\
\twait
```

The `kill 0` trap fires on Ctrl+C and cleans up both child processes. No new dep — `make` is the orchestrator.

## Gate

Standard project gate, run from the repo root:

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

`test_plugin_boundary.py` walks `sources/` and `services/`; the `api/`
package is framework code, so no test changes there. The boundary test
that asserts the framework doesn't reach into plugin internals is the
relevant guard; we don't import any plugin submodule from `api/`.

## Out of scope for v1

- Write endpoints (start/stop services, drive sessions, regenerate config).
- Auth (bearer token or otherwise).
- CORS.
- Pagination / filtering on the catalog endpoint.
- WebSocket / SSE for live status.
- Multi-worker uvicorn (`--workers > 1`). Single worker only; the in-process session registry is per-process.
- OpenAPI schema publication / docs UI.