# Orchestrator-driven service control — worker FastAPI contract

## Context

The Genesis orchestrator today reads from each worker over its existing FastAPI
surface (`:20985` by default) — `/v1/services`, `/v1/host`, `/v1/metrics`,
`/v1/paths`. There is no path for the orchestrator to install a service on a
worker, start/stop it, or push a configuration object the service should adopt
on start. Every write the orchestrator performs today goes through Ansible +
SSH (source deploy, lifecycle start/stop of the worker tmux session).

This document specifies the new POST endpoints the orchestrator needs so it
can drive a service on a worker, in particular the **bifrost** LLM gateway —
install it on a designated worker, start it with a `config.json` the
orchestrator composes from the rest of the fleet's llama-swap endpoints, and
refresh / restart it as the fleet changes. The same endpoints are general —
any docker or uv-tool declarative service is a candidate for orchestrator
control.

The endpoints land on the existing FastAPI process. No new port, no separate
control surface.

## Auth

**v1: no application-level auth.** Tailscale is the network gate, same as the
existing GET endpoints (ADR-033 in this repo). Bearer-token auth, when it
lands, will apply uniformly to both read and write endpoints — out of scope
for this change.

## Endpoints

### `POST /v1/services/{name}/install`

Install the service's primary installable if not already present. Idempotent.

- **Path param** `{name}` — service name (e.g. `bifrost`, `crawl4ai`).
- **Request body** — empty.
- **Response 200**:
  ```json
  {"installed": true, "version": "<version string or null>"}
  ```
- **Response 4xx / 5xx**:
  - `404` — `{name}` is not registered on this worker.
  - `409` — service has `can_install=False`, or its `installs()` list is empty
    (no primary installable).
  - `503` — installation already in progress from another request; caller
    should retry.

**Behavior**

- Already installed → no-op; return current state.
- Not installed → run the service's `primary_installable()`. May take minutes
  (docker image pull, etc.); the response blocks until complete.
- Service does not need to be stopped for install. The
  `uninstall_installable` guard (refuses if running) does not apply here.

### `POST /v1/services/{name}/start`

Start the service, materialising an optional configuration object before start.
Idempotent with respect to running state: if running, stop-then-start with the
new config; if not running, start; if not installed, install first.

- **Path param** `{name}`.
- **Request body** (optional):
  ```json
  {"config": <any JSON object>}
  ```
  - Omitted or `null` → start with the service's existing on-disk configuration
    (no materialisation).
  - Present → worker is responsible for materialising `config` into the format
    and on-disk location the service expects. The orchestrator does not know
    the layout; the worker dispatches per-service on `{name}`.

- **Response 200** — same shape as the existing `StartResult`:
  ```json
  {"ok": true, "message": "...", "pid": 1234}
  ```

- **Response 4xx / 5xx**:
  - `404` — service not registered.
  - `409` — service exists but is not startable in the current state for
    reasons other than "not installed" (e.g. binary missing for a non-install
    path). The orchestrator should surface `detail` to the user.
  - `500` — start failed after materialisation. The on-disk config has been
    written and the service is stopped. Caller surfaces this and does not
    retry blindly.

**Behavior**

- Not installed → install first (delegates to the install path; may take
  minutes).
- Running → stop, then start with the new config.
- Not running → start.
- Materialisation happens before stop so the freshly-started process picks up
  the new config on next launch.
- The orchestrator considers a successful response as authoritative; it does
  not poll `/v1/services/{name}/status` immediately after, though the gateway
  page may show a brief "starting…" state from a follow-up poll.

### `POST /v1/services/{name}/stop`

Stop the service if running. Idempotent.

- **Path param** `{name}`.
- **Request body** — empty.
- **Response 200** — same shape as `StopResult`:
  ```json
  {"ok": true, "message": "..."}
  ```
- **Response 4xx**:
  - `404` — service not registered.

**Behavior**

- Graceful stop using the service's existing lifecycle.
- Not running → no-op; returns `ok=true`.

### `POST /v1/services/{name}/restart`

Convenience for stop + start with optional config. Equivalent in outcome to
the orchestrator issuing stop then start, but avoids a brief window where the
service is stopped and the next start has not yet happened.

- **Path param** `{name}`.
- **Request body** — same as start (`{"config": ...}` optional).
- **Response 200** — same shape as start.
- **Behavior** — implementation can be a single atomic operation, or a stop
  followed by a start; either is acceptable. Same error semantics as start.

## Error body shape

All error responses use FastAPI's default `{"detail": "..."}` body. The
orchestrator surfaces `detail` to the user verbatim in failure toasts.

## Validation expectations

- `{name}` must match a registered service. Unknown names → 404.
- The service's capability flags (see `ServiceCapabilities` in
  `contracts/service.py`) gate which endpoints it accepts:
  - `can_install=False` → POST `/install` returns 409.
  - A service without `installs()` (no primary installable) → POST `/install`
    returns 409.
- Config object shape is **service-defined**. The orchestrator passes through
  whatever it built. The worker dispatches based on `{name}` to figure out
  where to write it. Worker authors add per-service materialisation logic;
  this contract does not constrain it.

## Example: bifrost start body

The orchestrator builds a config object with one entry per detected llama-swap
server, keyed by worker hostname:

```json
{
  "config": {
    "providers": {
      "openai": {
        "keys": [{"name": "openai-key", "value": "env.OPENAI_API_KEY", "models": ["*"], "weight": 1.0}]
      },
      "yoga": {
        "keys": [{"name": "yoga-key", "value": "dummy", "models": ["*"], "weight": 1.0}],
        "network_config": {
          "base_url": "http://yoga.local:8080",
          "default_request_timeout_in_seconds": 60
        },
        "custom_provider_config": {
          "base_provider_type": "openai",
          "allowed_requests": {
            "chat_completion": true,
            "chat_completion_stream": true
          }
        }
      }
    }
  }
}
```

The bifrost entry shape is fixed by bifrost upstream; the orchestrator does
not interpret it. The worker, on receiving this body for service `bifrost`,
materialises it to whatever location bifrost expects (its own concern) and
starts the bifrost container.

## What the orchestrator does NOT need

- No `GET /v1/agent_config` endpoint. The orchestrator derives each
  provider's `base_url` from the existing `/v1/services` response field
  `runtime_endpoint` on the `llama_swap` entry. Per-model metadata is not
  part of the bifrost provider entry shape.
- No bulk endpoint. The orchestrator issues one POST at a time.

## Testing expectations

- Each new endpoint should have at least: happy path, `404` on unknown name,
  idempotent re-call.
- Start should additionally cover: install-if-missing delegation, restart
  when running.
- Tests should use a stub `InferenceService` (no docker / no tmux) returning
  canned `StartResult` / `StopResult` / install outcomes. This keeps the
  contract tests hermetic — they exercise the endpoint wiring, not the
  lifecycle plumbing.
- The plugin-boundary test (`test_plugin_boundary.py`) is unaffected: the
  new endpoints live in `genesis_worker/api/routes/services.py`, which is
  framework code.

## Out of scope for this change

- Auth / authorization.
- Multi-orchestrator coordination. v1 assumes one orchestrator drives one
  worker at a time; not designed for HA.
- Bulk endpoints (`POST /v1/services:bulk`).
- Webhook / event push for state changes. Orchestrator continues to poll.
- Cancellation of in-flight install or start operations.
