# Plan 054: Enabled-set convergence across the UI and API processes

**Repo:** `genesis-agent-backend` (worktree `worktrees/t-jmu05l-enabled-set/genesis-agent-backend`,
branch `se/t-jmu05l-enabled-set`, from `origin/main` @ `4eee079`)
**Task:** `t-jmu05l-enabled-set` — *worker enabled-set does not converge across UI and API
process without a restart*.
**ADR:** `docs/arch/adr-054-enabled-set-convergence.md`
**Status:** implemented — the plan below is the execution reference for ADR-054.

> The approved plan was written when `049` was the next free ADR/plan number. `049` is now
> `adr-049-service-level-install.md` on `main`, `050` is reserved by another branch's plan
> gate, `051`/`052`/`053` are taken or in flight. Both numbers are therefore renumbered to
> `054` (ADR-054, plan-054); nothing else in the plan changed.

---

## 1. Goal

Make the worker's **enabled set** converge across its two processes without an
operator action: after a user flips a service in the Streamlit Service Catalog
page, `GET /v1/services` on the FastAPI process must report the new
`is_enabled` value on the **next request** — no
`systemctl restart genesis-worker-api.service`, no new endpoint, no pidfile,
no supervisor.

Secondary goal (hardening, same change): make the write path safe now that two
live processes read and write the same state file.

Non-goals: the ADR-029 bootstrap rule (a hand-started container is never
auto-enabled — a separate task), the toolkit's monitor, the systemd units, and
any new write endpoint on the API.

## 2. The divergence, in code

`genesis_worker/registries.py`:

- `ServiceRegistry.__init__` → `self._enabled = self._load_or_bootstrap_enabled_set()`
- `_load_or_bootstrap_enabled_set` → `load_enabled_set(state_dir)`; if the file
  exists it is **read once** and cached in `self._enabled` for the life of the
  process.
- `enable()` / `disable()` → mutate `self._enabled` **and** persist. The
  *calling* process is now correct; the file is now correct.
- `is_enabled()` / `enabled_names()` / `enabled()` / `disabled()` → read the
  in-memory set only. **No re-read, ever.**

Consequence: the UI process writes the file, the API process never looks at it
again → `/v1/services` keeps reporting `is_enabled=False` for the flipped
service until the API process restarts.

## 3. The convergence mechanism

**The state file is authoritative; every process re-reads it when the file's
identity changes (stat-guarded lazy refresh).**

1. `ServiceRegistry` stops owning an authoritative in-memory set. It owns a
   *cache* of the file plus the file identity it was built from.
2. Every read path refreshes first — `is_enabled()`, `enabled_names()`,
   `enabled()`, `disabled()`. The refresh is one `os.stat()`; when the identity
   is unchanged it is a no-op, when it changed the file is re-read and the
   cache is replaced.
3. Writers do read-modify-write against the freshest file: `enable()` /
   `disable()` refresh *before* mutating, then persist.
4. The API refreshes once per request (`get_worker()` dependency) and
   `/v1/services` renders **one snapshot** for the whole response.
5. `disable()`'s running-guard is untouched — it stays the first statement of
   `disable()`, before any refresh, before any mutation.
6. The bootstrap stays one-shot (ADR-029). It still happens at registry
   construction, and only there — a refresh never re-bootstraps.

Convergence guarantee: after the writer's `os.replace` returns, the next read
of the set by any process observes the new content.

## 4. Design details

### 4.1 `genesis_worker/utils/state/enabled_services.py` (extend)

```python
FileKey = tuple[int, int, int]        # (st_ino, st_mtime_ns, st_size)

@dataclass(frozen=True)
class EnabledSetSnapshot:
    key: FileKey | None                # None → no file present
    names: frozenset[str]
    readable: bool                     # False → file missing or unparsable
```

- `enabled_set_path(state_dir) -> Path` — the one definition of the file path.
- `read_enabled_snapshot(state_dir) -> EnabledSetSnapshot` — opens the file
  once, `os.fstat()`s **the open descriptor**, and reads the bytes from that
  same descriptor. Because `save_enabled_set` publishes via `os.replace`, an
  already-open descriptor still refers to the *old* inode → the reader always
  gets a **consistent snapshot of exactly one version** of the file.
- `enabled_set_key(state_dir) -> FileKey | None` — the cheap `os.stat()` probe.
- `load_enabled_set(...)` / `save_enabled_set(...)` keep their current
  signatures and semantics; `load_enabled_set` is re-implemented on top of
  `read_enabled_snapshot`.

`st_ino` is in the key because `save_enabled_set` writes a *fresh* tmp file and
`os.replace`s it, so **every** publish lands a new inode — the key cannot miss a
write even on a filesystem with coarse mtime granularity. `st_mtime_ns` and
`st_size` stay in the key as secondary signals.

**Write-path hardening.** `save_enabled_set` currently uses a *fixed* tmp name
(`enabled_services.yaml.tmp`). With two live processes that is a genuine race:
two writers can write the same tmp path concurrently and one can `os.replace`
a file the other is still writing → a published file that is a byte-level
mixture of two YAML documents → the loader treats it as malformed. Fix: a
unique tmp name per write (`{name}.{pid}.{uuid4}.tmp`). Keep `os.replace` +
mode `0600`.

### 4.2 `genesis_worker/registries.py` — `ServiceRegistry` (edit)

State held by the registry changes from "the truth" to "a cache of the file":
`self._enabled` (cached contents), `self._enabled_key` (identity of the
version we hold), `self._enabled_key_valid` (False → must re-read).

| Member | Behaviour |
|---|---|
| `_refresh_enabled_set() -> bool` | If the key is valid and the file's current key equals it → no-op. Otherwise read the snapshot: readable → adopt the names + key, return whether the set changed; **not** readable → keep the current set, remember the observed key. Never re-bootstraps. |
| `refresh_enabled_set() -> bool` | Public wrapper (used by the API dependency). |
| `is_enabled(name)` / `enabled_names()` / `enabled()` / `disabled()` | Refresh first, then answer. `enabled_names()` still returns a copy. |
| `enable(name)` | `KeyError` on unknown service (unchanged) → refresh → idempotence check → add → `save_enabled_set` → mark the key invalid. |
| `disable(name)` | **running-guard first, unchanged** (`svc.is_running()` → `RuntimeError`) → refresh → idempotence check → discard → `save_enabled_set` → mark the key invalid. |

After a mutation the key is marked **invalid** rather than re-stat'ed: a
concurrent writer could publish between our `os.replace` and our `stat`, and
caching *their* key against *our* content would serve stale data.

### 4.3 `genesis_worker/api/deps.py` (edit)

`get_worker()` calls `worker.services.refresh_enabled_set()` — one `stat()` per
request, making "one refresh per request" an explicit boundary.

### 4.4 `genesis_worker/api/routes/services.py` (edit)

`list_services` takes **one** snapshot (`worker.services.enabled_names()`) and
passes it to `_summary`, which answers `info.name in enabled`. A flip landing
mid-request cannot produce a half-old/half-new list.
`GET /v1/services/{name}` keeps `is_enabled(name)` (single service).

### 4.5 No systemd / toolkit changes

The two units are untouched — the restart requirement disappears because the
API re-reads on access. The toolkit's `BackendApiMonitor` is untouched: it
already reads `is_enabled` from `/v1/services` and drops disabled services.

## 5. Failure modes

| # | Failure mode | Behaviour |
|---|---|---|
| F1 | Stale read window | Bounded by "next read", not by a timer. |
| F2 | Flip lands mid-response | One snapshot per response + one refresh per request. |
| F3 | Lost update (two processes flip different services) | `enable()`/`disable()` refresh before mutating → both flips survive. |
| F4 | Same-service concurrent flip | Last writer wins (unchanged from ADR-033's acknowledged race). |
| F5 | Torn read | Impossible: `os.replace` publish + single open descriptor. |
| F6 | File deleted while a process runs | The reader keeps its last known set; it never re-bootstraps. |
| F7 | File corrupted | The reader keeps its last known set; it never re-bootstraps. |
| F8 | The running-guard | `disable()` still raises `RuntimeError` before any refresh or mutation. |
| F9 | Disabled service started by the orchestrator | Pre-existing semantics: the enabled set is a *visibility* flag. |
| F10 | Reader's `is_available()` differs from the writer's | Irrelevant after construction: availability only feeds the one-shot bootstrap. |
| F11 | Filesystem without stable inodes | Low: the state dir is local; mtime+size remain as secondary signals. |
| F12 | Multi-worker uvicorn | Each worker re-reads the file → all converge. |
| F13 | A reader that never reads | Nothing to converge — the next read picks up the current file. |

## 6. Files touched

**New:**

| Path | Purpose |
|---|---|
| `genesis_worker/tests/test_enabled_set_convergence.py` | The convergence test-suite (the proof test lives here). |
| `docs/arch/adr-054-enabled-set-convergence.md` | The ADR. |
| `docs/arch/plans/plan-054-enabled-set-convergence.md` | This plan. |

**Edited:**

| Path | Change |
|---|---|
| `genesis_worker/utils/state/enabled_services.py` | `enabled_set_path`, `enabled_set_key`, `read_enabled_snapshot` (+ `EnabledSetSnapshot`); `load_enabled_set` re-implemented on top of the snapshot; `save_enabled_set` gets a unique tmp name. |
| `genesis_worker/registries.py` | `ServiceRegistry`: cache + identity key, `_refresh_enabled_set()`, public `refresh_enabled_set()`, refresh-on-read, read-modify-write in `enable`/`disable`, bootstrap records the key. `disable()`'s guard untouched. |
| `genesis_worker/api/deps.py` | `get_worker()` refreshes the enabled set once per request. |
| `genesis_worker/api/routes/services.py` | `list_services` renders one snapshot per response. |
| `genesis_worker/tests/test_enabled_services_state.py` | Tests for the new file-level primitives. |
| `docs/arch/adr-033-read-only-fastapi.md` | Partial-supersession annotation on the "Two-process model" section. |
| `docs/arch/adr-029-service-catalog.md` | Pointer from the enable/disable section to ADR-054. |
| `genesis_worker/ui/services_catalog.py` | Docstring note: a flip is visible to the API process on its next request. |

**Not touched**: the systemd units, the infrastructure toolkit, ADR-029's
bootstrap rule, `genesis_worker/api/schemas.py` (the wire shape is unchanged),
the plugin contract.

## 7. QA plan

```
uv run ruff check --fix genesis_worker
uv run ruff format genesis_worker
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

Targeted run:

```
uv run pytest -q genesis_worker/tests/test_enabled_set_convergence.py \
                  genesis_worker/tests/test_enabled_services_state.py \
                  genesis_worker/tests/test_services_registry_enable_disable.py \
                  genesis_worker/tests/test_api.py
```

Hermeticity: every test roots its `PathsSettings` at `tmp_path`; no real
subprocess, no docker, no network.

### The proof test

`genesis_worker/tests/test_enabled_set_convergence.py::test_api_services_reflects_ui_enable_without_restart`
— two `GenesisWorker` instances over one shared `state_dir` (the in-process
model of the two processes), the API side driven through
`fastapi.testclient.TestClient` against the real `create_app()`. The test
fails on `main` (the API-side registry keeps its construction-time set) and
passes with the fix.

### Live verification on the host (after deploy; manual, not a repo test)

```bash
systemctl show genesis-worker-api.service -p MainPID --value   # note the pid
# flip a service in the Streamlit UI (Service Catalog → toggle)
curl -s localhost:20985/v1/services | jq '.[] | select(.name=="vaultwarden") | {name, is_enabled}'
systemctl show genesis-worker-api.service -p MainPID --value   # unchanged
```

## 8. Rollout

1. Implement on `se/t-jmu05l-enabled-set` (branch cut from `main` @ `4eee079`).
2. ADR first (AGENTS.md flow), then the code + tests, then the four gate
   invocations.
3. Merge to `main` (`git merge --no-ff`) and push.
4. Deploy to the host via the toolkit's `deploy_backend.yml` (rsync + `uv sync`
   + restart of both units) and run the live verification.

## 9. Open items

- **Mechanism**: stat-guarded re-read on access, over signalling and shared
  memory. The failure-mode analysis and the proof test are unaffected by the
  choice.
- **ADR-029's bootstrap gap** ("a hand-started container is never
  auto-enabled") is deliberately not addressed here — a separate decision
  about the bootstrap rule.
- **ADR-033's "acknowledged race"** is kept as-is (same-service
  last-writer-wins) rather than solved with a lock.
