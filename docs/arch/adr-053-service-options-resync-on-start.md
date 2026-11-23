# ADR-053: The options sidecar is authoritative at start time

## Title

The options sidecar is authoritative at start time — the service instance
is a cache of it, refreshed when it drifts.

## Status

Accepted (2026-11-23). Task `t-5wcd53` (sidecar-reread). Plan:
[`docs/arch/plans/plan-053-service-options-resync-on-start.md`](../plans/plan-053-service-options-resync-on-start.md).

Numbered 053 rather than the 050 the plan first proposed: 049 landed on
`main` (ADR-049, service-level install), 050 is claimed by the in-flight
backup task (`t-6zg9rq`), 051 landed on `main` (ADR-051, declared service
ports) and 052 is claimed by `t-au025m`. The plan's rule — take a number no
sibling task has claimed — still holds.

## Context

ADR-036 gave every declarative service a typed `options:` block, a
per-service override JSON sidecar (`<config_dir>/services/<name>.overrides.json`),
a rebuild path (`ServiceRegistry.rebuild`) and a `configure` UI panel that
persists and rebuilds. ADR-046 added a second writer: an orchestrator config
body may carry an `options` section, which the worker writes to the same
sidecar and then rebuilds against.

Two writers, one store, and the store is read **live** by
`Settings.options_for("services", name)` — but only at *construction* time.
The registry builds each service once and caches the instance; the option
values are then baked into the resolved spec (`DockerServiceConfig.extra_env`,
the `volumes` block, every `$options.X` substitution).

The start path never re-derives them. `GenesisWorker.start_service` calls
`_apply_orchestrator_options`, which rebuilds **only** when the request body
carries an `options` section; with no body it returns the cached instance and
starts *it*. So an operator who edits the sidecar directly — the documented
escape hatch for the map-typed options, and the only way to set a value the
panel does not expose — gets a **silent no-op**: the container comes back with
the options the worker loaded at boot, and nothing says otherwise. This was
observed on 2026-11-16 on the omarchy box while fixing the speaches UI
(task `t-c329yu`).

The shape is not new: ADR-029's enabled set had the same problem (read once at
construction, cached forever), and the parallel change for it (ADR-049 on the
`se/t-jmu05l-enabled-set` branch) settles the general rule — *the on-disk
state is authoritative; the in-memory copy is a cache, refreshed when the disk
changed*.

Three ways to close the gap existed:

(a) **Log loudly** when the on-disk sidecar differs from the in-memory options
and change nothing else. Cheap, but leaves the operator with a warning and no
way to make the edit take effect — the edit stays a no-op for the only action
most operators take (restart).

(b) **Always rebuild** the service on every start. Correct, but replaces the
instance on every start even when nothing changed, re-parses the YAML and
re-runs the ADR-045 port-adoption pass, and invalidates references other code
holds to the instance.

(c) **Rebuild when the on-disk options differ** from the ones the instance was
built from. Same outcome as (b) when something changed, no churn when nothing
did.

## Decision

We will treat a service's **on-disk option state as authoritative** and the
in-memory service instance as a **cache** of it.

1. `ServiceRegistry` records the option slice each instance was built from
   (`_option_inputs`), and updates that record when it rebuilds the instance.
   The recorded slice is the *merged* slice (`{**flat-file slice, **sidecar**}`)
   that `Settings.options_for` returns.
2. `ServiceRegistry.refresh_options(name)` compares the recorded slice with the
   slice the disk yields now. Because the flat-file slice is identical on both
   sides (it is a snapshot inside the `Settings` object), the comparison
   isolates the **sidecar delta exactly**.
3. When the slices differ, the service is **rebuilt**
   (`ServiceRegistry.rebuild`) before the start — construct-first, swap-last,
   the same mechanism ADR-036 defined. When they do not, the cached instance is
   used unchanged: no rebuild, no instance churn.
4. **Every start re-reads** — `GenesisWorker.start_service` calls
   `refresh_options` after the stop, so a plain start, a plain restart, a start
   with an orchestrator body, the UI's Start button and the CLI all behave the
   same way.
5. The rebuild happens **after** the pre-stop, because a rebuild of a running
   service is refused (ADR-036); the port records survive the rebuild, so a
   service keeps its host port across the restart.
6. **Python services are out of scope of the rebuild.** A Python service has no
   declarative spec path and therefore no generic rebuild path (ADR-036); its
   own pages write the sidecar and update the instance in place.
   `refresh_options` returns such a service unchanged.
7. A **corrupt sidecar fails loudly** on the start path (a `RuntimeError`
   carrying the parser's message and the file path), not silently.
8. The orchestrator-options path (ADR-046) is **unchanged**: a body with an
   `options` section still writes the sidecar and rebuilds; a body without one
   still changes nothing *by itself* — but the service's options now converge
   with the **sidecar**, which is a different statement (ADR-046's bullet is
   annotated in place).

We will **not** add cross-process IPC, a file watcher, a new endpoint, or a
"reload the worker" mechanism; and we will **not** extend the rebuild path to
Python services.

## Consequences

**Positive:**

- A direct sidecar edit is honoured by the next start/restart: the container's
  env, mounts and `$options.X`-derived values match the file the operator
  edited. The silent no-op is gone.
- The store has one authority and one refresh point. "What is running" is
  always derivable from the files on disk plus the running containers.
- The instance is only replaced when something actually changed, so the common
  case (a restart with no edits) keeps the object identity, the allocated host
  port and the adoption records.
- The rule reads as one with ADR-049's: *the disk is authoritative, the
  in-memory copy is a cache*.

**Negative:**

- The start path now performs one extra disk read (the sidecar) and, when the
  sidecar changed, one extra YAML load + instance construction. The start
  already spawns a container; the added cost is negligible.
- A start is no longer guaranteed to be a pure "restart of what is running": it
  is "restart of what is running **as configured on disk now**". An operator
  who edits the sidecar and then restarts gets the edit — that is the point,
  but it changes the meaning of the action.
- A corrupt sidecar now fails the start (with a readable message) instead of
  being ignored. It already broke the next worker boot, so this makes the
  failure visible earlier rather than adding a new failure mode.

**Neutral:**

- The flat `user-overrides.env` slice is still a snapshot inside `Settings`; a
  direct edit of *that* file is not picked up by a start. That is a separate
  defect with a separate fix (the Configure panel's scalar options have the
  same gap) and is deliberately not addressed here.
- The API wire shape is unchanged; only the effect of a body-less restart
  changes.
- Python services keep their current behaviour: no generic rebuild, no
  sidecar convergence.

## Plan

[`docs/arch/plans/plan-053-service-options-resync-on-start.md`](../plans/plan-053-service-options-resync-on-start.md)
