# ADR-036: User-editable service config — env, mounts, ports, and a `configure` UI panel

## Title

User-editable service config — three new option types (`port`, `env_map`, `mount_map`), a per-service override JSON sidecar, a `$options.X` substitution extension, a `worker.rebuild_service` mechanism, and a `configure` UI panel that auto-generates a form from the service's options schema.

## Status

Accepted.

## Context

ADR-035 introduced declarative YAML services with a typed `options:` block. Options reach the running container via `$options.X` placeholders, resolved at construction against `ctx.options`. User edits flow through `<config_dir>/user-overrides.env` (ADR-034), which is a flat KEY=VALUE file.

That system covers the things the YAML author pre-declares as typed options (crawl4ai's `jwt_enabled`, bifrost's `log_level`). It does **not** cover everything the user reasonably expects to adjust from the UI:

- **Volumes (bind mounts).** Photoprism, for example, mounts `/photoprism/originals` to expose a directory of pictures to index. The host path is a per-deployment choice — every operator wants a different one. Today it's a YAML-static field; changing it means editing the YAML.
- **Ports.** `listen_port` is the host-side port the container binds. Off-the-shelf defaults exist, but operators frequently move them to avoid collisions or to publish through a reverse proxy. Today: YAML-static.
- **ENV variables.** Every docker service has a long tail of upstream env vars beyond the typed knobs the YAML author pre-declared. Photoprism has ~30; llama-swap's recipe model has hundreds. Today: YAML-static, no escape hatch.
- **Lifecycle for the changes.** Even if the user could edit the right values somewhere, the `ServiceRegistry` builds instances once in `__init__` and caches them; the `DockerServiceConfig` is `frozen=True`. There is no rebuild path. Restarting the worker re-reads everything, which works but is heavyweight.

### Forces

- **Don't duplicate the `options:` mechanism.** The Settings page's "Extra overrides" textarea already lets the user set arbitrary `KEY=VALUE` pairs. Adding a second, parallel free-form editor for env/mounts would give the user two places to edit one thing and force the framework to reconcile.
- **Stay declarative.** A Python subclass per service to render a bespoke config form would defeat the whole point of the YAML template (ADR-035). The form must be auto-generated from the options schema.
- **Backward compat.** bifrost, crawl4ai, sillytavern already ship with `options:` blocks that flow through `user-overrides.env`. The new mechanism must not break that path.
- **The `$variable` substitution has an explicit allow-list** (ADR-035 table). Lifting the carve-out for `listen_port` / `listen_host` / `internal_port` is the right move but needs to be a deliberate decision, not a side effect.
- **Persistence format.** The flat `user-overrides.env` can't represent nested dicts (env vars, mount pairs). A per-service JSON sidecar is the natural fit for map types. Putting scalars in the sidecar too would be cleaner but breaks the Settings page's flat-key UI.
- **Restart semantics.** Editing `listen_port` and applying it does not change the running container's bind address. The user needs to restart to apply. We can't silently restart on every save — that would surprise the user with restarts they didn't ask for.

### Decisions the user already settled

The conversation before this ADR settled five open questions. Recording them here so future readers don't re-litigate:

1. **Defaults containing `$variable` markers** (e.g. `default: "$data_dir/.."` for `pictures_dir`): the loader runs `resolve_placeholders` on the dumped options dict **before** the spec walk, so the substitution reaches the final value.
2. **`ui_group` strings**: free-form section names ("Network", "Storage", "Security", "Advanced"). Friendlier for YAML authors; the form renders the heading as-is.
3. **Scalar persistence location**: scalars stay in `user-overrides.env` for backward compatibility with bifrost/crawl4ai/sillytavern. Map types (`env_map`, `mount_map`) live in a per-service JSON sidecar.
4. **Apply-and-restart atomicity**: on failure, the new config persists and the service stays stopped. No rollback. The user explicitly asked for the change; they own the failure.
5. **`$options.X` substitution scope**: all four of `listen_port`, `listen_host`, `internal_port`, `public_host`.

## Decision

We will extend the declarative-service system so the user can adjust **any field the YAML author exposes** from a UI panel, with persistence and a hot-reload path for the in-memory service instance.

### 1. Three new option types in `spec.py`

`_OPTION_TYPE_BUILDERS` gets three more entries. Each builder follows the existing pattern (`optional` adds `| None`, `default` provides the field default).

- **`port`** — `int` with `gt=0` and `lt=65536` validation. The pydantic field is built with `Field(..., gt=0, lt=65536)` instead of a bare `int`. The DSL stays closed (no new validation hooks; "port" is the only numeric-range type today).
- **`env_map`** — `dict[str, str]`. Always optional with default `{}` because the long tail is opt-in. Pydantic coerces YAML mappings; the JSON sidecar populates the user's additions.
- **`mount_map`** — `dict[str, Path]`. The key is the container path (string), the value is the host path. Path coercion on the host side. Same opt-in default `{}`.

Existing types are unchanged. The closed-DSL principle is preserved — exotic option shapes still require a Python `options.py` companion.

### 2. UI metadata on `OptionSpec`

Three new optional fields, all strings:

```yaml
options:
  listen_port:
    type: port
    default: 2342
    ui_label: Web UI port
    ui_help: Host-side port the web UI listens on
    ui_group: Network
```

The form uses `ui_label` as the widget label (falls back to the option name), `ui_help` as a tooltip/help paragraph, and `ui_group` as the section heading (free-form string). No behaviour change for services that omit these fields — the form renders a default label, no tooltip, and groups everything under "General".

### 3. `$options.X` substitution extended

The ADR-035 table carve-out for `listen_port` / `listen_host` / `internal_port` / `public_host` is lifted. The substitution machinery is already generic; only the doc says "no". Concretely: every field in the `DockerServiceSpec` is `$options.X`-substitutable; the loader walks the entire resolved spec, not a field allow-list.

The `$data_dir` / `$state_dir` / `$log_dir` / `$cache_dir` / `$config_dir` / `$vault_path` substitutions are unchanged.

### 4. Second-pass option resolution in `loader.py`

```python
probe = options_model(**ctx.options)               # typed values
options_dump = probe.model_dump()                   # dict
options_resolved = resolve_placeholders(            # NEW: substitute $variable markers in option defaults
    options_dump, ctx, options_dump
)
resolved = resolve_placeholders(spec.model_dump(), ctx, options_resolved)
```

The new pass lets an option default like `"$data_dir/.."` flow through to its resolved host path before it lands in `config.extra_volumes`. Without it, the resolved value would be the literal string `"$data_dir/.."`, which docker would reject.

### 5. Map options merge into the resolved config

After both resolution passes, the loader merges `options_resolved["extra_env"]` and `options_resolved["extra_mounts"]` additively into `resolved["env"]` and `resolved["volumes"]`. **User additions win on key collisions** — if the user adds `PHOTOPRISM_UPLOAD_NSFW: "false"` to `extra_env` and the YAML typed knob resolved to `"true"`, the user value wins. Rationale: the typed knob is the discovery surface; `extra_env` is the long tail; the user picked the long tail deliberately.

For volumes: same rule. User additions in `extra_mounts` merge into `resolved["volumes"]`. A key collision (user wants a different host path for a YAML-declared container mount) means the user's path wins.

### 6. Per-service override JSON sidecar

`<config_dir>/services/<name>.overrides.json` — one file per declarative service. Contains only the **map-typed** options (`extra_env`, `extra_mounts`, plus any future map types). Format:

```json
{
  "extra_env": {"PHOTOPRISM_SITE_URL": "https://photos.example.com"},
  "extra_mounts": {"/photoprism/cache": "/srv/cache"}
}
```

Atomic write (mode `0o600` like `user-overrides.env`). Missing file → empty dict. Malformed JSON → loud error.

Two new helpers in `genesis_worker/utils/config_overrides.py`:

- `read_service_overrides(path) -> dict[str, Any]`
- `write_service_overrides(path, values) -> None`

`Settings.options_for("services", name)` reads the sidecar and merges its contents into the returned dict alongside the flat-key overrides. Concretely: the flat-key overrides contribute scalar options; the sidecar contributes map options; both feed `ctx.options` at construction.

### 7. `ServiceRegistry.rebuild(name)`

The registry now tracks the spec path per service in a `_spec_paths: dict[str, Path]` map. `rebuild(name)`:

1. Refuses if the service is currently running (caller is responsible for stopping first; "Apply & restart" wraps this with stop + start).
2. Removes the cached instance.
3. Re-runs `load_service_spec(spec_path, ctx=self._context_for_name(name))`.
4. Stores the new instance.

If the loader raises (bad YAML, missing path, etc.), the old instance is left in place and the exception propagates. **Construct-first, swap-last** — same rule `refresh_config` follows on the worker facade.

`worker.rebuild_service(name)` on the `GenesisWorker` facade exposes the operation to the UI.

### 8. `configure` UI dialog

A new registered panel kind in `genesis_worker/utils/services/panels.py`. The status page renders a single `Configure` button; clicking it opens a modal (`@st.dialog`) containing the auto-generated form:

| Field type | Widget |
|---|---|
| `int` / `port` | `st.number_input` with min/max from the pydantic field |
| `float` | `st.number_input` |
| `bool` | `st.checkbox` |
| `string` | `st.text_input` |
| `path` | `st.text_input` with a directory picker if streamlit supports it (v1: text input + tooltip) |
| `list[string]` | comma-separated `st.text_input` parsed back to list on save |
| `list[int]` | comma-separated `st.text_input` parsed to `[int, ...]` |
| `list[path]` | multi-line text area, one path per line |
| `env_map` | key/value pair editor (add/remove rows) |
| `mount_map` | container-path / host-path pair editor |

The form lives in a dialog rather than dumping inline on the status
page — operational info (start / stop, container state, logs) stays
in focus, and the dense form only appears when the operator asks
for it.

Form is grouped by `ui_group`. Buttons:

- **Apply** — persist to the right file(scalar → `user-overrides.env`, maps → JSON sidecar), call `worker.rebuild_service(name)`. Closes the dialog; the running container still uses the old config until restart.
- **Apply & restart** — Apply + stop (if running) + start (if was running). If the new start fails, the config stays persisted and the service stays stopped. No rollback.

The "stale running config" banner from the inline-prototype was
removed when we moved to a dialog — the dialog closes after Apply,
so the user no longer has the form visible to remind them to
restart. The user reopens the dialog or restarts from the service's
start/stop controls on the status page.

### 9. Worked example: photoprism

`genesis_worker/services/_declarative/photoprism.yaml` adopts the new pattern. See the plan file for the resulting YAML. The existing `test_photoprism_identity` test updates to match the new options structure.

bifrost, crawl4ai, sillytavern are **unchanged** — they don't need `extra_env` / `extra_mounts` for now and continue to work through their existing `options:` + `$options.X` flow. The `configure` panel auto-includes for any service that declares `options:`, so all three of these services get a configure form on their status page even though their YAMLs don't add it to `ui.status_panels` explicitly.

### 10. Documentation

`docs/tutorials/declarative-services.md` gets a new section documenting the three new types, the UI metadata fields, the per-service override JSON sidecar, the `$options.X` substitution for `listen_port` and friends, and the `configure` panel. Existing sections unchanged.

## Consequences

**Positive**

- The user can adjust any field the YAML author exposes from a single per-service UI panel. No Python subclass needed for configurability.
- Photoprism (and any future docker service) gets a sensible UX for bind mounts, port, and env tweaks without bespoke UI code.
- The free-form `extra_env` / `extra_mounts` escape hatches the long tail of upstream env vars (photoprism has ~30; llama-swap recipes have hundreds) without bloating the YAML author's options schema.
- The closed-type DSL stays closed; the three new types are additive.
- Backward compatibility for bifrost / crawl4ai / sillytavern: their existing `options:` flow is untouched.
- Service-level changes (config edits) take effect without restarting the worker — the next `start()` picks up the new config. "Apply & restart" automates the full loop.

**Negative**

- **Two persistence files per service.** Scalars in `user-overrides.env`, maps in `<config_dir>/services/<name>.overrides.json`. The Settings page's flat-key UI doesn't know about the sidecar, so a user who edits scalars through "Extra overrides" and maps through the configure panel sees two files for one service. We document this clearly in the tutorial.
- **User wins on key collisions** between YAML-declared env vars and `extra_env`. A user who adds `PHOTOPRISM_UPLOAD_NSFW: "false"` to `extra_env` while the typed knob `upload_nsfw: true` also exists gets the long-tail value. This is a documented behaviour but a source of surprise; we mitigate by putting `extra_env` under a clearly-labeled "Advanced" group.
- **The `configure` form lives in a modal dialog**, not inline on the status page. The status page stays focused on operational info; the form only appears when the operator asks for it via the `Configure` button. The trade-off: the form is no longer visible at all times, so after `Apply` (which doesn't restart) the operator has to either reopen the dialog or use the status page's start/stop controls to apply the new config. The "stale running config" banner from the inline-prototype is no longer needed.
- **`$options.X` substitution for `listen_host` / `internal_port` / `public_host`** widens the placeholder system. The substitution machinery is generic and type-preserving, so the widening is safe — but anyone who reads the docs and assumes "no `$options` in container fields" (per ADR-035) will be wrong. We update the docs in the same change.
- **Loader does one extra substitution pass.** Negligible cost (a small dict walk) but it does add a step. Documented in `loader.py`.

**Neutral**

- The `configure` panel is a new entry in the panels registry. Existing panels (`service_info`, `container_info`, `auth_token`, `log_tail`) are unchanged. The panel auto-includes for any service that declares `options:` — YAML authors don't need to add `- configure` to `ui.status_panels` explicitly.
- The panel order changed for docker services: YAML-declared panels (`configure`, `auth_token`, etc.) now land right after `service_info`, before the default `container_info` and `log_tail`. Previously they appended at the bottom. This is a small UX improvement (the user-editable form is visible without scrolling) but a visible change for crawl4ai's auth_token position.
- The per-service JSON sidecar lives under `<config_dir>/services/`, a new subdirectory. Empty dir on first run.
- `worker.rebuild_service` is the first facade method that's destructive enough to refuse when running. We document the pattern; future "regenerate this thing in place" methods follow the same convention.

## Plan

`docs/arch/plans/plan-036-user-editable-service-config.md` — file-by-file execution in seven independently testable phases:

1. Spec & DSL extensions (`port`, `env_map`, `mount_map`, UI metadata).
2. Loader enhancements (second-pass option resolution, map merge, broadened substitution).
3. Per-service override JSON sidecar (helpers, settings merge).
4. Service registry rebuild (`rebuild(name)`, facade exposure).
5. `configure` UI panel (panel kind, form generator, persist + restart helpers).
6. Worked example — photoprism.yaml adopts the new pattern.
7. Documentation update (tutorial section).
