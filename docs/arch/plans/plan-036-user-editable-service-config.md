# Plan: ADR-036 — user-editable service config

Implements ADR-036. Seven independently committable phases. Each phase
ends with the test/type/lint gate green and a runnable worker.

## Phase 1 — Spec & DSL extensions

Foundation only. No behaviour change for existing services. The new
types sit dormant in the DSL until later phases wire them in.

### 1.1 New option types in `_OPTION_TYPE_BUILDERS`

**`genesis_worker/utils/services/spec.py`** — three new entries:

```python
def _build_port_field(optional: bool, default: Any) -> Any:
    if optional:
        return (int | None, default)
    return (int, Field(default if default is not None else 0, gt=0, lt=65536))

_OPTION_TYPE_BUILDERS = {
    ...
    "port": lambda opt, default: _build_port_field(opt, default),
    "env_map": lambda opt, default: (dict[str, str] | None, default or {}),
    "mount_map": lambda opt, default: (
        dict[str, Path] | None,
        {k: Path(v) for k, v in (default or {}).items()} or {},
    ),
}
```

`mount_map` value coercion: when the user supplies a path string from
the JSON sidecar, coerce to `Path` so the resolved volume target is a
proper Path object (matching the behaviour of `path`).

### 1.2 UI metadata on `OptionSpec`

Same file. Add three string fields to `OptionSpec`:

```python
class OptionSpec(BaseModel):
    ...
    ui_label: str = ""
    ui_help: str = ""
    ui_group: str = ""
```

`extra="forbid"` keeps existing YAMLs parsing (the new fields default
to empty strings).

### 1.3 Tests

**`genesis_worker/tests/test_yaml_spec.py`** — already covers
`_OPTION_TYPE_BUILDERS`. Extend it:

- `test_port_field_validates_range` — `OptionsModel(port=99999)` raises
  `ValidationError`; `OptionsModel(port=0)` raises; `OptionsModel(port=2342)` passes.
- `test_env_map_default_empty_dict` — `OptionsModel()` with `extra_env: { type: env_map, default: {} }`
  exposes `extra_env == {}`.
- `test_mount_map_coerces_path_values` — `OptionsModel(extra_mounts={"/x": "/host/x"})` exposes
  `extra_mounts["/x"] == Path("/host/x")`.
- `test_ui_metadata_passes_through` — declare options with `ui_label` / `ui_help` / `ui_group`; verify
  they appear on the synthesised model field (read via `model_fields[name].json_schema_extra` or
  the field metadata).

Phase 1 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 2 — Loader enhancements

Three changes: option defaults resolve `$variable` markers, `listen_port` /
`listen_host` / `internal_port` / `public_host` become `$options.X`-substitutable,
map options merge into the config.

### 2.1 Second-pass option resolution

**`genesis_worker/utils/services/loader.py`** — `load_service_spec`:

```python
probe = options_model(**ctx.options)
options_dump = probe.model_dump()
options_resolved = resolve_placeholders(options_dump, ctx, options_dump)  # NEW
resolved = resolve_placeholders(spec.model_dump(), ctx, options_resolved)
```

The new line ensures option defaults that contain `$data_dir` /
`$state_dir` / etc. placeholders get resolved before they're used as
lookup values for `$options.X` in env / volumes / container fields.

### 2.2 Map option merge into config

Same file, in `load_service_spec` after `resolved` is computed:

```python
if isinstance(spec, DockerServiceSpec):
    extra_env = dict(options_resolved.get("extra_env") or {})
    extra_mounts_raw = options_resolved.get("extra_mounts") or {}
    extra_mounts = {k: Path(v) for k, v in extra_mounts_raw.items()}
    # User additions win on key collisions.
    merged_env = {**resolved.get("env", {}), **extra_env}
    merged_volumes = {**resolved.get("volumes", {}), **extra_mounts}
    resolved["env"] = merged_env
    resolved["volumes"] = merged_volumes
```

(For uv services, the equivalent merge would go into `install_env` /
`command_env`. Skipped in v1 because no uv service currently uses
`extra_env`. Documented in the tutorial.)

### 2.3 Broadened substitution scope

Already implicit in the substitution machinery (it walks the entire
resolved spec). No code change beyond removing the doc table entry.
Update the tutorial doc in phase 7.

### 2.4 Tests

**`genesis_worker/tests/test_yaml_loader.py`** — extend:

- `test_option_default_with_data_dir_resolves` — declare `pictures_dir: { type: path, default: "$data_dir/.." }`;
  build a service against a hermetic context; assert
  `svc.config.extra_volumes["/photoprism/originals"]` is the resolved data_dir parent.
- `test_listen_port_substitutes_option` — declare
  `container: { listen_port: $options.web_port, ... }` with `web_port: { type: port, default: 9090 }`;
  assert `svc.config.listen_port == 9090` and `probe.model_dump()["web_port"] == 9090`.
- `test_extra_env_merges_with_yaml_defaults` — declare an `extra_env` option of type `env_map`;
  pass `ctx.options["extra_env"] = {"FOO": "bar"}`; assert the resolved `svc.config.extra_env`
  contains both YAML-default and user-added entries, with user additions winning on collision.
- `test_extra_mounts_merges_and_coerces_paths` — same pattern for mounts; verify the values are
  coerced to `Path`.

Phase 2 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 3 — Per-service override JSON sidecar

Persistence format for map-typed options.

### 3.1 Helpers in `utils/config_overrides.py`

```python
SERVICE_OVERRIDES_DIRNAME = "services"

def service_overrides_path(config_dir: Path, name: str) -> Path:
    """Per-service override JSON sidecar location."""
    return config_dir / SERVICE_OVERRIDES_DIRNAME / f"{name}.overrides.json"

def read_service_overrides(path: Path) -> dict[str, Any]:
    """Parse a per-service overrides JSON file. Missing file → empty dict.

    Malformed JSON raises with the path and line/column from the parser
    so the caller can point the operator at the offending entry.
    """
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed service overrides at {path}: {exc}") from exc

def write_service_overrides(path: Path, values: dict[str, Any]) -> None:
    """Atomic write of a per-service overrides JSON file. Mode 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(values, indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.chmod(0o600)
    os.replace(tmp, path)
```

### 3.2 Settings merge

**`genesis_worker/settings.py`** — `options_for` reads the sidecar and
merges:

```python
def options_for(self, axis: str, name: str) -> dict[str, Any]:
    base = dict(getattr(self, axis).get(name, {}))
    if axis == "services":
        from .utils.config_overrides import read_service_overrides, service_overrides_path
        sidecar = read_service_overrides(service_overrides_path(self.paths.config_dir, name))
        # Maps merge on top of scalars so the user-added long tail wins.
        return {**base, **sidecar}
    return base
```

The merge order is scalars-first, maps-second because the flat-key
path contributes scalars (which the user typed into the Settings
"Extra overrides" textarea) and the sidecar contributes maps. The two
are disjoint in practice (flat keys can't represent nested dicts), so
the merge is effectively additive.

### 3.3 Tests

**`genesis_worker/tests/test_user_overrides_io.py`** — extend with:

- `test_read_service_overrides_missing_returns_empty`.
- `test_read_service_overrides_malformed_raises_clear`.
- `test_write_service_overrides_creates_parent_dir`.
- `test_write_service_overrides_atomic_and_mode_600`.
- `test_options_for_merges_sidecar` — construct `Settings` with paths
  pointing at a tmp dir; write a sidecar JSON; assert `options_for("services", "x")` returns
  the merged dict.

Phase 3 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 4 — Service registry rebuild

Hot-reload path for the cached service instance.

### 4.1 Track spec paths

**`genesis_worker/registries.py`** — `ServiceRegistry.__init__`:

```python
self._spec_paths: dict[str, Path] = {}

# In the YAML loop:
ctx = self._context_for_name(spec_path.stem)
svc = load_service_spec(spec_path, ctx=ctx)
self._spec_paths[svc.name] = spec_path
```

### 4.2 `rebuild(name)` method

Same file:

```python
def rebuild(self, name: str) -> InferenceService:
    """Re-construct ``name`` against fresh options. Refuses if running.

    Construct-first, swap-last: if the loader raises, the existing
    cached instance is untouched and the exception propagates.
    """
    spec_path = self._spec_paths.get(name)
    if spec_path is None:
        raise KeyError(f"unknown service: {name}")
    old = self._instances.get(name)
    if old is not None and old.is_running():
        raise RuntimeError(
            f"cannot rebuild {name!r} while running — stop the service first"
        )
    new_svc = load_service_spec(spec_path, ctx=self._context_for_name(name))
    self._instances[name] = new_svc
    return new_svc
```

### 4.3 Facade exposure

**`genesis_worker/facade.py`** — one method on `GenesisWorker`:

```python
def rebuild_service(self, name: str) -> None:
    """Re-construct a single service against current options.

    The running container, if any, is unaffected — it continues with
    its old config. Use ``start_service(name)`` after ``stop_service(name)``
    to pick up the new config.
    """
    self._service_registry.rebuild(name)
```

### 4.4 Tests

**`genesis_worker/tests/test_services_registry.py`** — extend:

- `test_rebuild_replaces_cached_instance` — register a service; mutate
  the user-overrides env file; call `rebuild`; assert the cached instance identity changed and its config
  reflects the new options.
- `test_rebuild_refuses_when_running` — fake a running service; call
  `rebuild`; assert `RuntimeError`.
- `test_rebuild_preserves_old_instance_on_failure` — pass a malformed
  YAML override; call `rebuild`; assert the cached instance is unchanged.

Phase 4 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 5 — `configure` UI panel

The auto-generated form.

### 5.1 New panel kind

**`genesis_worker/utils/services/panels.py`** — register `configure`:

```python
@register("configure")
def _configure(svc: InferenceService, _panel_config: dict) -> None:
    from .configure_panel import render_configure_panel
    render_configure_panel(svc)
```

New file **`genesis_worker/utils/services/configure_panel.py`** with
`render_configure_panel(svc)`. The module is separate from `panels.py`
because the form-rendering logic is non-trivial (~150 lines).

The panel:

1. Reads `svc.options_model` to discover fields and groups by `ui_group`.
2. Renders each field with the appropriate widget (table in the ADR §8).
3. Reads current values from `worker.read_user_overrides()` for scalars and
   `worker.read_service_overrides(name)` for maps. Prefills the widgets.
4. "Apply" button → `worker.write_user_overrides(scoped_scalars)` + `worker.write_service_overrides(name, maps)`
   + `worker.rebuild_service(name)`. Streamlit rerun shows new defaults.
5. "Apply & restart" button → Apply + `worker.stop_service(name)` (if running)
   + `worker.start_service(name)` (if was running).
6. Banner: detect "stale running config" by comparing the rebuilt `svc.config.listen_port` /
   `svc.config.image_repo` against `svc.is_running()` and a fresh
   `docker inspect`. Implementation note: keep the detection cheap — compare `listen_port` and
   the sorted env keys; full deep equality is too expensive on every rerun.

### 5.2 Facade helpers for the UI

**`genesis_worker/facade.py`** — three new methods:

```python
def read_service_overrides(self, name: str) -> dict[str, Any]:
    """Read the per-service override JSON sidecar for ``name``.

    Returns the raw dict (which may contain map-typed entries that
    don't fit the flat ``user-overrides.env`` shape).
    """
    from .utils.config_overrides import read_service_overrides, service_overrides_path
    return read_service_overrides(service_overrides_path(self._settings.paths.config_dir, name))

def write_service_overrides(self, name: str, values: dict[str, Any]) -> None:
    """Atomic write of the per-service override JSON sidecar."""
    from .utils.config_overrides import service_overrides_path, write_service_overrides as _w
    _w(service_overrides_path(self._settings.paths.config_dir, name), values)
```

(The scalars path already exists: `worker.write_user_overrides(values)`.
The configure panel calls it with the subset of keys that are scalars
for the service, namespaced as `GENESIS_SERVICES__<NAME>__<OPTION>`.)

### 5.3 Form implementation notes

`render_configure_panel` is a pure function of `svc` + the worker in
`st.session_state["worker"]`. Tests can drive it with a fake worker.

Widget choices:

- `port` → `st.number_input(..., min_value=1, max_value=65535, step=1)`.
- `int` → `st.number_input(..., step=1)`.
- `float` → `st.number_input(..., step=0.1, format="%.2f")`.
- `bool` → `st.checkbox`.
- `string` → `st.text_input` (multiline when the option name ends in `_text` or `_multiline`? — no, just text input for v1).
- `path` → `st.text_input` (Streamlit 1.30+ has `st.text_input` with no native picker; users paste a path).
- `list[string]` → `st.text_area` with comma-separated values, parsed back.
- `list[int]` → `st.text_area` with comma-separated integers, parsed back.
- `list[path]` → `st.text_area` with one path per line.
- `env_map` → custom add/remove rows widget. State stored in `st.session_state[f"env_map_rows_{name}"]`.
- `mount_map` → same pattern, two columns per row.

The widget state lives in `st.session_state` keyed by `(service_name, option_name)` so multiple
panels don't collide.

Persistence helpers:

```python
def _scoped_overrides(svc: InferenceService, scalars: dict[str, Any]) -> dict[str, str]:
    """Convert a per-service scalars dict to flat ``GENESIS_SERVICES__<NAME>__<KEY>`` keys."""
    out = {}
    for k, v in scalars.items():
        if isinstance(v, bool):
            out[f"GENESIS_SERVICES__{svc.name.upper()}__{k.upper()}"] = "true" if v else "false"
        else:
            out[f"GENESIS_SERVICES__{svc.name.upper()}__{k.upper()}"] = str(v)
    return out
```

This mirrors what the Settings page does for the framework knobs.

### 5.4 Tests

**`genesis_worker/tests/test_configure_panel.py`** — new file. Tests
drive `render_configure_panel(svc)` with a mocked Streamlit context and a
fake worker. Assertions cover:

- `test_render_scalar_options` — string / int / bool / port fields render as the expected widgets.
- `test_render_map_options_with_add_remove_rows` — env_map and mount_map render with row editors.
- `test_apply_writes_and_rebuilds` — simulate Apply click; verify write_user_overrides + write_service_overrides + rebuild_service were called with the right args.
- `test_apply_and_restart_calls_stop_and_start` — simulate Apply & restart; verify the stop+start sequence.
- `test_banner_appears_when_stale` — running service with a port different from the rebuilt config; verify the banner renders.
- `test_unknown_option_type_falls_back_to_text_input` — synthetic options_model with an unknown
  field; verify graceful fallback (no crash).

Phase 5 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 6 — Worked example: photoprism.yaml

### 6.1 Adopt the new pattern

**`genesis_worker/services/_declarative/photoprism.yaml`** — see the
ADR §9 for the resulting YAML. Specifically:

- Replace `volumes["/photoprism/originals"]` value `"$data_dir/.."` with
  `"$options.pictures_dir"`.
- Replace `container.listen_port: 2342` with `container.listen_port: $options.listen_port`.
- Add `container.listen_host` and `internal_port` as-is (no change).
- Add typed options `listen_port`, `pictures_dir`, `admin_password`, `upload_nsfw`,
  `extra_env`, `extra_mounts` with UI metadata.
- Add `configure` to `ui.status_panels` so the panel renders on the status page.

### 6.2 Test updates

**`genesis_worker/tests/test_declarative_specs.py`** — extend
`test_photoprism_identity`:

```python
def test_photoprism_identity(tmp_path: Path) -> None:
    svc = _load_spec("photoprism.yaml", tmp_path)
    assert svc.name == "photoprism"
    assert svc.config.listen_port == 2342  # default
    # options exposes the typed knobs
    assert svc.options.listen_port == 2342
    assert svc.options.upload_nsfw is True
    # volumes resolves the picture_dir default
    originals = svc.config.extra_volumes["/photoprism/originals"]
    assert originals.endswith("/..")
    assert svc.config.extra_env["PHOTOPRISM_UPLOAD_NSFW"] == "true"
    # env / volume defaults merge with the (empty) extra_env / extra_mounts
    assert "extra_env" not in svc.config.extra_env  # user-additive, not present by default
    # ui_panels includes configure
    assert "configure" in svc.ui_panels
```

Phase 6 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 7 — Documentation

### 7.1 Tutorial update

**`docs/tutorials/declarative-services.md`** — append a new section
"User-editable service config" covering:

- The three new types (`port`, `env_map`, `mount_map`) with examples.
- The UI metadata fields (`ui_label`, `ui_help`, `ui_group`).
- The `$options.X` substitution extension to `listen_port` /
  `listen_host` / `internal_port` / `public_host` (replace the ADR-035 carve-out note).
- The per-service override JSON sidecar (`<config_dir>/services/<name>.overrides.json`)
  and when it's used vs `user-overrides.env`.
- The `configure` UI panel: how to add it to a service, what widgets render for each type,
  the Apply / Apply & restart buttons.
- A worked example for photoprism (link to the YAML).

Update the existing "Options block" section to mention `port` / `env_map` / `mount_map` and the UI metadata fields.

### 7.2 Cross-reference

**`docs/arch/adr-035-declarative-service-template.md`** — annotate the
"container.listen_host, listen_port — No" carve-out as superseded by
ADR-036. Inline annotation only; do not change ADR-035's status.

Phase 7 gate: pytest, pyright, ruff check, ruff format --check.

---

## Final gate

After phase 7:

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

All four must pass. The configure panel must be reachable end-to-end:
launch the worker, enable photoprism, navigate to its status page,
edit `pictures_dir` and `listen_port`, click Apply, restart, verify
the container binds the new port and mounts the new directory.
