# Plan 053: The options sidecar is authoritative at start time

**Repo:** `genesis-agent-backend` (worktree `worktrees/t-5wcd53-sidecar-reread/genesis-agent-backend`,
branch `se/t-5wcd53-sidecar-reread`, from `origin/main` @ `4eee079`)
**Task:** `t-5wcd53` — *worker: a plain service restart does not re-read the options sidecar*
**ADR:** `docs/arch/adr-053-service-options-resync-on-start.md`
**Status:** implemented — the plan below is the execution reference for ADR-053.

---

## 1. Goal

A **direct edit of a service's options sidecar** (`<config_dir>/services/<name>.overrides.json`)
must take effect on the next **plain** start/restart — `POST /v1/services/<name>/restart`
with **no body**, and the equivalent UI Start / restart action.

Today that edit is a **silent no-op**: the container comes back with the options the
worker process happened to load at boot.

Required QA: *a direct sidecar edit followed by a plain restart must change the running
container's env.*

Non-goals: changing the persistence format, changing the Configure panel's persistence
split, re-reading the flat `user-overrides.env` on the start path (a separate, adjacent
defect), and giving Python services (llama-swap, ComfyUI) a generic rebuild path.

## 2. The code path

```
POST /v1/services/speaches/restart            (no body)
  → api/routes/services.py::restart_service    body=None → config = None
  → facade.py::GenesisWorker.restart_service(name, config=None)
  → facade.py::GenesisWorker.start_service(name, config=None)
      svc = self._service_registry.get(name)          # ← the CACHED instance
      if svc.is_running(): svc.stop()
      svc = self._apply_orchestrator_options(name, None)
          section = (config or {}).get("options")      # → None
          if not isinstance(section, dict) or not section:
              return self._service_registry.get(name)  # ← RETURNS THE CACHED INSTANCE
      svc.start()                                      # renders from the STALE config
```

`facade.py::_apply_orchestrator_options` is the only place that rebuilds a service, and it
only does so **when the request body carries an `options` section**. With no body it
returns the cached instance, so the sidecar is never re-read on that path.

The sidecar *is* read live elsewhere: `Settings.options_for("services", name)` merges the
flat-file slice with `read_service_overrides(...)` — a fresh disk read on every call — and
`ServiceRegistry._context_for_name` calls it when it builds a service's `ServiceContext`.
The sidecar is authoritative **at construction time**; the bug is that the start path
never *constructs* anything.

## 3. The decision

**The sidecar is authoritative; the service instance is a cache of it. Every start
re-reads it and rebuilds when it differs.**

1. `ServiceRegistry` remembers the option slice each instance was built from
   (`self._option_inputs[name] = dict(ctx.options)`), recorded at construction and updated
   by `rebuild()`.
2. `ServiceRegistry.refresh_options(name)` compares that recorded slice with the slice the
   disk yields *now* (`self._settings.options_for("services", name)` — a live sidecar
   read). Equal → return the cached instance (no churn). Different → `rebuild(name)` and
   return the new instance. Python services (no declarative spec path) are returned
   unchanged — they have no generic rebuild path (ADR-036).
3. `GenesisWorker.start_service` calls `refresh_options(name)` after the stop and after
   `_apply_orchestrator_options(...)`, so **every** start — plain or with a body, from the
   API, the UI or the CLI — starts from an instance that matches the disk.

Because `Settings.options_for` merges the (stale but *identical on both sides*) flat-file
slice under the live sidecar, the comparison isolates the **sidecar delta exactly**.

### Alternatives rejected

| Option | Why it loses |
|---|---|
| (a) Log loudly when the sidecar differs, change nothing | Does not satisfy the required QA; the codebase has no logging infrastructure, and ComfyUI's own pages write the sidecar routinely, so every ComfyUI restart would "warn". |
| (b) Always rebuild on every start | Replaces the instance on every start even when nothing changed (YAML re-parse + model build + the ADR-045 adoption pass) and invalidates references other code holds. |
| (c) Stat-guard the sidecar and rebuild on an identity change | Same as (b) but with filesystem-identity assumptions to answer a question a 1 KB file read answers exactly. The start path is cold. |
| (d) Re-read the flat `user-overrides.env` too | A different defect; drags ADR-034's precedence rules into this change. |
| (e) Patch the live instance's options in place | `extra_env` / `extra_mounts` are merged into the *resolved* `env` / `volumes` blocks and `$options.X` markers are substituted at construction; patching the model in place leaves the resolved config stale. |
| (f) Restart the worker process on every sidecar edit | The edit happens outside the worker's control. |

## 4. File-by-file

| File | Change |
|---|---|
| `genesis_worker/registries.py` | `ServiceRegistry`: `_option_inputs` bookkeeping, `options_drifted()`, `refresh_options()`, `rebuild()` records the slice it built from. |
| `genesis_worker/facade.py` | `GenesisWorker.start_service`: call `refresh_options()` before the start; docstring updates. |
| `genesis_worker/tests/test_service_options_resync.py` | The new behaviour + the required proof test. |
| `docs/arch/adr-053-service-options-resync-on-start.md` | The ADR. |
| `docs/arch/adr-046-orchestrator-supplied-service-options.md` | Inline annotation on the bullet *"A config body without an `options` section changes nothing"* — it now means "the orchestrator's push changes nothing"; the **sidecar** is re-read on every start. |
| `docs/tutorials/declarative-services.md` | Correct the *"never re-resolved at start time"* sentence and document the new rule: *edit the sidecar → restart the service → the change is live*. |

**Not touched**: the API schemas/routes' wire shape, the Configure panel, the persistence
helpers (`utils/config_overrides.py`), `Settings`, the plugin contract, the systemd units,
the infrastructure toolkit.

## 5. Error handling

`read_service_overrides` raises `ValueError("malformed service overrides at <path>: …")`
for a corrupt sidecar. On the new path that would escape `start_service` as a `ValueError`,
which the route layer does not translate (→ an opaque 500). The refresh wraps it so the
failure surfaces as a `RuntimeError` with the operator-readable message, which the route
already maps to `500` + `detail` (ADR-038's error contract):

```
RuntimeError: cannot apply the on-disk options for 'speaches': malformed
service overrides at /home/…/services/speaches.overrides.json: …
```

## 6. Tests

New file `genesis_worker/tests/test_service_options_resync.py`:

| Test | Proves |
|---|---|
| `test_restart_after_direct_sidecar_edit_applies_the_new_env` | **the proof test** — a direct sidecar edit + a plain restart changes the container's env |
| `test_post_restart_without_body_applies_a_direct_sidecar_edit` | the same through the HTTP surface |
| `test_start_after_direct_sidecar_edit_applies_the_new_env` | the plain **Start** path converges too |
| `test_restart_without_sidecar_change_keeps_the_service_instance` | no spurious rebuild — the instance identity survives a no-op restart |
| `test_restart_after_sidecar_removal_drops_the_option` | removing the sidecar reverts the option to the spec default |
| `test_restart_with_an_options_body_still_pushes_the_values` | regression for ADR-046 (write + rebuild) through the **start** path |
| `test_restart_of_a_python_service_with_a_drifted_sidecar_does_not_raise` | llama-swap (no declarative spec) is returned unchanged |
| `test_restart_with_a_malformed_sidecar_fails_loudly` | a corrupt sidecar raises a `RuntimeError` naming the file |
| `test_orchestrator_options_on_a_python_service_still_raises` | regression: ADR-046's "applying options to a Python service raises" is untouched |

Regression sweep: `test_facade.py`, `test_api.py`, `test_configure_panel.py`,
`test_traefik_service.py`, `test_services_registry.py`, `test_declarative_specs.py`,
`test_yaml_loader.py`.

## 7. QA gate

```
uv run ruff check --fix genesis_worker
uv run ruff format genesis_worker
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

## 8. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | The rebuild replaces the service instance; code holding the old object sees stale options | low | The facade re-reads the instance after the refresh; the identity only changes when the sidecar actually changed. |
| R2 | The drift check misses a change | low | The comparison is on the *content* of the merged slice, not on file identity. A change to the YAML spec file itself is out of scope. |
| R3 | Spurious rebuilds | very low | A rebuild with identical options is idempotent; the cost is a YAML parse. |
| R4 | The rebuild loses the service's host port | low | `rebuild()` calls `port_allocator.reconcile(name, markers)` — only markers the new spec no longer declares are dropped. |
| R5 | The start path now depends on the sidecar being readable | low | A corrupt sidecar already breaks the next worker boot; this surfaces it earlier with a readable message. |
| R6 | The start path now depends on the sidecar being **valid** against the option schema | low | Loud failure over silent corruption is the repo's stated preference (ADR-034). |
| R7 | ComfyUI / llama-swap behaviour changes | very low | No declarative spec path → `refresh_options` returns them unchanged. |
| R8 | The Configure panel's *Apply & restart* double-rebuilds | very low | The panel's `rebuild_service` updates the recorded slice, so `refresh_options` finds no drift. |

## 9. Out of scope — filed as follow-ups

1. The flat `user-overrides.env` is not re-read on the start path, so the Configure panel's
   scalar options never take effect until a `refresh_config()` or a worker restart.
2. Python services have no generic rebuild path (ADR-036) — a direct sidecar edit for
   llama-swap / ComfyUI still does not converge on a restart.
3. The enabled-set twin (`t-jmu05l`) — separate branch, separate ADR (049 on
   `se/t-jmu05l-enabled-set`). ADR-053 deliberately states the same principle.
4. The Configure panel still renders from the in-memory instance, so a hand-edit made
   *after* the last start is not shown in the form.
