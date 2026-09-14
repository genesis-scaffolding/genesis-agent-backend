# ADR-034: Runtime user-overrides file + in-place facade refresh

## Title

Runtime user-overrides file layered into Settings, with an in-place
`GenesisWorker.refresh_config()` rebuild and a new Streamlit Settings
page.

## Status

Accepted.

## Context

The worker resolves its config from a fixed precedence chain
(defaults → `dev.env` → `.env` → real env vars → constructor args;
documented at the top of `genesis_worker/settings.py`). Every source
of truth is **read-only at runtime**: a user who wants to change
`vault_path` on a deployed instance has no way to do so without
editing `.env` on the remote machine and bouncing the worker.

The orchestrator (`genesis-infrastructure-toolkit`) deploys the worker
via Ansible but exposes no knob for per-node overrides — `deploy_backend.yml`
sets `GENESIS_UI_PORT` and `GENESIS_API_PORT` and that's it. On
machines where the model vault is not at `~/models/` (e.g. the user's
`archdesktop`, where the actual cache lives somewhere else), a freshly
deployed worker boots with the wrong vault and the catalog comes back
empty.

We need three things:

1. **A persistent override layer.** Whatever the user sets must
   survive worker restarts.
2. **A way to apply changes live.** Editing `.env` requires a restart;
   that's annoying on a deployed instance and contradicts the worker's
   "manage everything from the UI" story.
3. **An operator hook (deferred).** The orchestrator will eventually
   need to drop per-node overrides into the install dir on deploy.
   Out of scope for this ADR.

Two structural facts shape the design:

- **`Settings` is constructed once** at facade init (`GenesisWorker.__init__`).
  Plugins (`ModelSource`, `InferenceService`) snapshot their options
  at construction (`self._options = LlamaSwapOptions(**ctx.options)`,
  `local_path = Path(options["local_path"])`). "Live updates" via
  in-place mutation of `Settings` only works for properties that
  aren't destructured at construction, which is almost none of them.
- **The plugin boundary is strict (ADR-009).** A plugin may import
  only `genesis_worker.contracts` and `genesis_worker.utils`. Adding
  an override mechanism must not extend the contract surface or give
  plugins a way to reach into the framework.

## Decision

We will add a **user-overrides file** at the highest precedence of
`Settings` construction, plus a **`refresh_config()` method on the
facade** that rebuilds the registries in place. The Streamlit UI
gets a new **Settings page** that reads and writes the override file
and triggers the refresh. Both Streamlit and FastAPI keep their
existing facade wiring unchanged — `refresh_config()` mutates the
shared singleton rather than swapping it.

### Override file location and format

- **Location:** `<config_dir>/user-overrides.env`. With XDG defaults
  this is `~/.config/genesis-worker/user-overrides.env`, sitting next
  to `enabled_services.yaml`. The file's directory already exists by
  the time the worker boots (the registry creates it when persisting
  the enabled set).
- **Format:** dotenv — one `KEY=VALUE` per line, `#` for comments.
  Two reasons. First, it mirrors `.env.example`, which the user (and
  the orchestrator) already understand. Second, the orchestrator's
  Ansible `lineinfile` module can write into it on deploy without
  YAML serialization. pydantic-settings' nested-delimiter syntax
  (`GENESIS_SOURCES__HUGGINGFACE__LOCAL_PATH`) is preserved as-is.
- **Atomic writes.** `write_user_overrides()` writes to
  `<file>.tmp`, chmods to `0o600`, then `os.replace`s into place.
  Mirrors the existing `enabled_services.yaml` pattern.
- **Mode.** `0o600`. The override file is operational state, not
  secrets, but it lives next to secrets on disk so the conservative
  default is fine.

### Precedence chain

The existing chain (defaults → `dev.env` → `.env` → real env →
constructor args) gains one layer:

```
defaults → dev.env → .env → real env → user-overrides.env → constructor args
```

Implemented by reading the override file ourselves and feeding it as
a dict to `Settings(_env_file=None, **env_values)` via pydantic-settings'
initialiser-kwarg path. (pydantic-settings' `env_file` accepts a tuple
but treats every entry as a file path on disk; we want the override
file's *contents* merged at the highest layer without writing it as
a real env var.) The Settings module grows a small helper
`_read_user_overrides_file(config_dir: Path) -> dict[str, str]` that
the constructor calls before instantiating pydantic-settings.

### GenesisWorker additions

Three read methods and one refresh method on the facade. All are
additive — no existing surface changes.

- `user_overrides_path() -> Path` — `<config_dir>/user-overrides.env`.
- `read_user_overrides() -> dict[str, str]` — current file contents
  parsed. Empty dict when the file is absent.
- `write_user_overrides(values: dict[str, str]) -> None` — atomic
  write. Caller's responsibility to merge with existing values if
  they want partial edits.
- `snapshot_settings() -> list[SettingSnapshot]` — for the UI's
  "effective settings" view. One entry per framework knob the user
  cares about (paths + per-source `local_path`s), each carrying the
  resolved value and the source it came from (default / `.env` /
  `user-overrides.env` / env / constructor). The existing dashboard
  debug panel can use the same data, replacing its hand-rolled env
  walk.
- `refresh_config() -> None` — re-read the override file, rebuild
  `Settings` and both registries in place, drop the in-memory
  catalog cache. **Preserves the `GenesisWorker` object's identity.**
  In-flight acquire sessions are **preserved** — they keep their old
  source-instance reference but continue to function because
  acquire state lives on the session object itself, not the source.
  Running services are unaffected (they're OS processes); their
  status queries re-check tmux/docker on every call.

### Refresh safety

`refresh_config()` constructs every new object **before** swapping
any attribute on the facade. If `Settings()` raises
(`pydantic.ValidationError`) or either registry raises (plugin
construction failure from a bad `vault_path`), the existing facade
is untouched and the exception propagates. A bad save = loud error,
no corruption.

The UI page's save handler wraps `refresh_config()` in a try/except
and surfaces the error inline. The override file may be on disk in
a state that Settings refuses — we tell the user explicitly so they
can fix the file by hand.

### New module: `genesis_worker/utils/config_overrides.py`

A leaf utility that holds the file-format helpers. Allowed import
surface: stdlib + third-party only. Specifically **not** allowed to
import `contracts`, `settings`, or any plugin module — the boundary
test already enforces the `utils` leaf invariant (see
`test_plugin_boundary.py::test_utils_is_a_leaf_package`). Functions:

- `read_user_overrides(path: Path) -> dict[str, str]` — parse a
  dotenv file. Missing file → empty dict. Malformed file → raises
  with the offending line.
- `write_user_overrides(path: Path, values: dict[str, str]) -> None`
  — atomic write with `0o600`. Stable ordering (sorted by key) so
  diffs are reviewable.

### Streamlit Settings page

`genesis_worker/ui/settings.py` — a framework page (not a plugin
page), registered in `ui/app.py` under the **Overview** section after
**Service Catalog**. Sidebar order becomes: Dashboard, Model Catalog,
Service Catalog, **Settings**.

The page exposes **framework knobs only** per the user's decision:

- **Paths:** `vault_path` and the five XDG dirs (data, config, cache,
  state, log).
- **Per-source local paths:** `huggingface.local_path`,
  `lmstudio.local_path`.

No service-specific knobs (e.g. `listen_addr`, `llama_server_variant`,
`kv_quant_over_bytes`) — those stay on each service's own Status
page. The user may use the page's "extra overrides" textarea for
unmodeled keys; this is a small affordance, not a commitment to
model every knob.

Three bordered sections:

1. **Effective settings** — read-only table. One row per known knob:
   `name`, resolved value, source badge (default / `.env` /
   `user-overrides.env` / env / constructor). Data from
   `worker.snapshot_settings()`.
2. **User overrides** — editable form. Each known knob has a
   checkbox ("Override") and an input bound to the override value.
   Below, a textarea for "Extra overrides" (`KEY=VALUE` per line)
   parsed by the same dotenv reader the file uses. **Save overrides**
   button writes the file and calls `refresh_config()`.
3. **Status after save** — banner explaining which services are
   currently running and need an explicit restart on their Status
   page to pick up new options. For framework-only edits (paths,
   per-source `local_path`) this banner is empty because no service
   options changed.

The page never restarts the worker. Restart UX is intentionally out
of scope (ADR deferred).

### API surface (later, if needed)

The read methods (`user_overrides_path`, `read_user_overrides`,
`snapshot_settings`) are already exposed via `worker.*` and reachable
through the FastAPI surface trivially. Write methods
(`write_user_overrides`, `refresh_config`) are not exposed via the
HTTP API in this change — read-only API posture is enforced by
ADR-033 and we don't extend it without a separate decision.

## Plan

`docs/arch/plans/plan-034-runtime-user-overrides.md`.

## Out of scope (deferred)

- One-click "Restart worker" button on the Settings page. The worker
  is launched via tmux by the Ansible playbook; the worker itself
  has no opinion about how it's supervised. A separate ADR will
  decide whether and how the worker exposes lifecycle control.
- Orchestrator-side hook (`deploy_backend(extra_env=...)`) for
  per-node overrides. The override file format is compatible
  (dotenv) so this is purely an orchestrator change.
- Service-specific knobs on the Settings page. Each service's
  Status page is the right home for its own options; a unified page
  becomes a maintenance burden as plugins evolve.
- Exposing write methods (`write_user_overrides`, `refresh_config`)
  via the HTTP API. ADR-033 keeps the API read-only.
- Per-plugin override files. ADR-009 has each plugin own its schema,
  so a separate file per plugin would be a defensible alternative —
  but the win is small (type-safe per-key validation) and the cost is
  N files + N read/write surfaces. Revisit if the user wants
  field-level validation.
- Secret overrides. `GENESIS_SECRETS__*` is its own access path
  (ADR-012) with the `SecretsAccessor` contract; folding it into the
  override file would conflate two precedence stories. Out.

## Consequences

**Positive**

- The user's stated pain point (wrong vault path on `archdesktop`)
  is fixed: a one-line edit on the Settings page, file written,
  refresh runs, next catalog walk visits the right directory.
- Override file persists across worker restarts (and across
  redeploys, because the file lives in `<config_dir>` not in the
  install tree). One-time fix, no operator intervention.
- The facade object identity is preserved across refreshes — pages
  and routes that hold a `worker` reference continue to work
  without coordination.
- Read methods are useful beyond the Settings page: the existing
  dashboard debug panel can replace its hand-rolled env walk with
  `worker.snapshot_settings()`. Same data, one implementation.
- Atomic write + mode 0o600 match the existing `enabled_services.yaml`
  precedent. Consistent operational hygiene.
- Layering into the existing precedence chain is additive — every
  deployment that worked before still works, with the override
  file simply not existing on those hosts.

**Negative**

- A refresh that changes service options (e.g. `listen_addr`) does
  not affect a running service until the user restarts it. The
  page surfaces this honestly. Not a new problem (same as editing
  `.env` today) but worth being explicit about.
- `refresh_config()` rebuilds registries; that's a small CPU cost
  on every save. Acceptable — saves are user-driven and rare
  compared to construction cost.
- Two surfaces (Streamlit + FastAPI) hold the same `GenesisWorker`
  singleton; both observe the refresh in the next event loop tick.
  No coordination needed because the facade is mutated in place.
- The dotenv reader is permissive by default (ignores unknown keys
  because pydantic-settings has `extra="ignore"`). Users can write
  junk into the override file and have it silently dropped. Same
  behaviour as `.env` today; consistent.
- `Settings()` is constructed twice on every refresh: once to build
  the new settings, once at the next `refresh_config()` call. Cheap
  but worth noting — if `Settings()` ever becomes expensive (e.g.
  if we add network probes), we may need to revisit.

**Neutral**

- The contract surface (`genesis_worker.contracts`) is unchanged.
- Plugin code is unchanged. `ctx.options` arrives via the new
  registry construction as before.
- The test boundary (`test_plugin_boundary.py`) is unaffected —
  the new utility lives under `utils/` and the new code lives under
  the framework's existing imports.