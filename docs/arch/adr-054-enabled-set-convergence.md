# ADR-054: Enabled-set convergence across the worker's two processes

## Title

Enabled-set convergence across the worker's two processes — the state file
is authoritative and re-read on access.

## Status

Proposed.

## Context

The worker runs as two processes that share one state file and no memory:
the Streamlit UI (`genesis-worker-ui.service`) owns the `ServiceRegistry`
instance the Service Catalog page flips, and the FastAPI process
(`genesis-worker-api.service`) owns the instance `/v1/services` reads.
ADR-033 made the on-disk state the only shared state between them and
explicitly acknowledged the write race on `enabled_services.yaml`.

ADR-029 put the enabled set in `<state_dir>/enabled_services.yaml`, persisted
on every `enable()`/`disable()`, and bootstrapped it once from
`is_available()`. But the registry *read* that file exactly once, at
construction, and cached the result in memory forever. A flip in the UI
therefore persisted to the file and left the API process serving the old
set: `GET /v1/services` kept reporting `is_enabled=False`, the
infrastructure toolkit's `BackendApiMonitor` dropped the service, and no
ingress route or Traefik config was produced. Only
`systemctl restart genesis-worker-api.service` re-read the file.

Three mechanisms were considered:

(a) signal the API process to reload — SIGHUP (uvicorn handles only
SIGINT/SIGTERM, so an unhandled SIGHUP *terminates* the API; needs a
handler, a pidfile and a new cross-process contract), a reload endpoint
(needs the UI to learn the API's address — the two units are configured
independently — and adds an unauthenticated write endpoint to a read-mostly
API), or a background mtime-watch thread in the API (adds poll latency and
a new silent-divergence failure mode: a dead watcher means stale forever);
(b) re-read the file on access / on a short TTL in the reader;
(c) a shared in-memory store between the processes (a new IPC service, a new
deployment artifact, and ADR-033 rules out cross-process IPC).

## Decision

We will make `enabled_services.yaml` **authoritative** and treat every
process's in-memory enabled set as a **cache** of it.

1. The cache is keyed by the state file's identity — `(st_ino, st_mtime_ns,
   st_size)`. Because the file is published with `os.replace`, every write
   lands a new inode, so the identity changes on every write even when the
   content and the timestamp do not.
2. Every read of the enabled set (`is_enabled`, `enabled_names`, `enabled`,
   `disabled`) re-checks the identity and re-reads the file when it changed.
   A read of the set is therefore never more than one file version behind.
3. Readers read the file through a single open descriptor and `fstat` that
   descriptor, so a reader always observes exactly one version of the file,
   never a partial write.
4. Writers (`enable`, `disable`) refresh before mutating: the mutation is
   applied to the freshest file content, so concurrent flips of *different*
   services do not clobber each other. Concurrent flips of the *same*
   service remain last-writer-wins.
5. The API refreshes the set at the request boundary and renders one
   snapshot per response, so a flip landing mid-request cannot produce a
   mixed list.
6. `disable()`'s running-guard is unchanged and remains the first statement
   of the method: a running service cannot be disabled, in any process.
7. The bootstrap rule (ADR-029) is unchanged and remains one-shot: it runs
   only at registry construction, and never on a refresh. A missing or
   corrupt file at refresh time leaves the last known set in place — it
   never re-bootstraps and never wipes the set.
8. `save_enabled_set` publishes through a uniquely named temporary file, so
   two concurrent writers can never share one.

We will **not** add cross-process IPC, a shared-memory store, a reload
endpoint, a signal handler, or a background watcher thread.

## Consequences

**Positive:**

- A UI flip is visible in `/v1/services` on the next request — no restart,
  no new endpoint, no new deployment artifact, no new port.
- The mechanism is self-healing: a reader that was down, idle or started
  before the flip converges on its next read. Nothing is signalled and
  nothing can be lost.
- The toolkit needs no change; it already consumes `is_enabled` correctly
  and starts seeing the new value on its next fleet refresh.
- Works with multi-worker uvicorn: each worker re-reads the file
  independently.
- The write path is hardened against the concurrent-writer tmp-file race
  that the two-process model made reachable.
- Hand-editing the state file now takes effect without a restart
  (config-as-code becomes possible; ADR-029 deferred it for lack of a
  convergence mechanism) — except inside an open Streamlit session, which
  writes its stale toggle value back (see the Negative consequences).

**Negative:**

- The enabled set is no longer a process-local constant: reads now perform a
  `stat()` (one syscall) and occasionally a YAML parse. A `/v1/services`
  request refreshes twice — once in the `get_worker()` dependency and once
  for the response snapshot — so the cost is two `stat()`s per request, not
  one per service.
- The file becomes the source of truth, so a hand-edit can disable a
  service fleet-wide. Unknown names in the file are still ignored when the
  registry resolves them (ADR-029), and a malformed file leaves the last
  known set in place rather than wiping it.
- An open Streamlit session reverts an out-of-band flip. The Service
  Catalog toggle keeps its own session state and writes that value back on
  the next interaction, so a flip made elsewhere — the API process, the
  CLI, a hand-edit of the state file — is undone by the next interaction in
  an already-open session, and stands only once the session resets. The
  API process is unaffected: it re-reads the file per request. Fixing the
  toggle's write-back is a separate change; this ADR only corrects the
  record of what the current code does.
- The last-known-set rule means a deleted or corrupt file leaves a reader
  reporting a set that no longer matches the disk. This is deliberate: the
  alternative (re-bootstrap) silently re-enables services.
- Two processes now genuinely write the file. The read-modify-write rule
  removes the lost-update class of races; same-service races remain
  last-writer-wins.

**Neutral:**

- The enabled set stays a *visibility* flag, orthogonal to the running
  state: a service that is disabled and then started by the orchestrator
  runs but stays invisible to the dashboard and gets no ingress route.
- ADR-029's bootstrap semantics are untouched; only the *reading* of the
  file changed.
- The API's wire shape is unchanged (`is_enabled` was already part of the
  service summary schema).

## Plan

`docs/arch/plans/plan-054-enabled-set-convergence.md`.
