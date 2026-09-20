# Plan: ADR-037 — media vault path

Implements ADR-037. Seven independently committable phases. Each phase ends with the test/type/lint gate green and a runnable worker.

## Phase 1 — `media_vault_path` on `PathsSettings`

Foundation only. No behaviour change for existing services or the model vault. The new field sits dormant in the settings schema until later phases wire it in.

### 1.1 Add the field and the resolved accessor

**`genesis_worker/settings.py`** — extend `PathsSettings`:

```python
class PathsSettings(BaseModel):
    data_dir: Path = Field(default_factory=lambda: xdg_path("DATA", ".local/share", XDG_BASE))
    config_dir: Path = Field(default_factory=lambda: xdg_path("CONFIG", ".config", XDG_BASE))
    cache_dir: Path = Field(default_factory=lambda: xdg_path("CACHE", ".cache", XDG_BASE))
    state_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state", XDG_BASE))
    log_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state", XDG_BASE))

    vault_path: Path | None = None
    media_vault_path: Path | None = None  # NEW

    @property
    def resolved_vault_path(self) -> Path:
        if self.vault_path is not None:
            return self.vault_path
        legacy = _read_models_root()
        if legacy is not None:
            return Path(legacy)
        return Path.home() / "models"

    @property
    def resolved_media_vault_path(self) -> Path:  # NEW
        if self.media_vault_path is not None:
            return self.media_vault_path
        return Path.home() / "media"
```

Default: `~/media` (matches the user's chosen separation from XDG). Override via `GENESIS_PATHS__MEDIA_VAULT_PATH`.

### 1.2 Settings layer merges the override

**`genesis_worker/settings.py`** — update `_resolve_user_overrides_path` so the override file resolves correctly when only the new key is set. The existing path resolution already covers `GENESIS_PATHS__MEDIA_VAULT_PATH` via pydantic-settings' `env_nested_delimiter` — no change needed.

### 1.3 Facade surfaces the new knob

**`genesis_worker/facade.py`** — `snapshot_settings` gains a `media_vault_path` row:

```python
path_knobs: list[tuple[str, Path, str]] = [
    ("vault_path", paths.vault_path or paths.resolved_vault_path, "GENESIS_PATHS__VAULT_PATH"),
    ("media_vault_path", paths.media_vault_path or paths.resolved_media_vault_path,
     "GENESIS_PATHS__MEDIA_VAULT_PATH"),  # NEW
    ("data_dir", paths.data_dir, "GENESIS_PATHS__DATA_DIR"),
    ...
]
```

`_merge_paths_with_overrides` learns the new key:

```python
mapping = {
    "GENESIS_PATHS__VAULT_PATH": "vault_path",
    "GENESIS_PATHS__MEDIA_VAULT_PATH": "media_vault_path",  # NEW
    ...
}
```

### 1.4 Update `.env.example`

**`.env.example`** — append a commented block:

```bash
# ─── Media vault (ADR-037) ─────────────────────────────────────────────────
# Root directory for content produced by services (ComfyUI inputs/outputs,
# PhotoPrism-indexed pictures, future media peers). Defaults to ``~/media``
# to keep user-produced content visibly separate from the worker's XDG state.
#GENESIS_PATHS__MEDIA_VAULT_PATH=/home/you/media
```

### 1.5 Tests

**`genesis_worker/tests/test_settings.py`** — extend:

- `test_settings_default_media_vault_path` — with no env vars, `paths.resolved_media_vault_path == Path.home() / "media"`.
- `test_settings_resolved_media_vault_path_explicit` — `GENESIS_PATHS__MEDIA_VAULT_PATH=/srv/media` resolves to `/srv/media`.
- `test_settings_resolved_vault_path_unchanged_by_media_vault` — regression: `vault_path` resolution is unaffected by `media_vault_path` (and vice versa).

**`genesis_worker/tests/test_facade.py`** — extend:

- `test_snapshot_settings_includes_media_vault_path` — `snapshot_settings` returns an entry whose `name == "paths.media_vault_path"`.
- `test_options_for_paths_merge_picks_up_media_vault_path` — write `GENESIS_PATHS__MEDIA_VAULT_PATH=/tmp/media` to the override file; `refresh_config`; assert the snapshot reflects the new value.

Phase 1 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 2 — `media_vault_path` on `ServiceContext`

Wire the new path into the plugin context so services can read `ctx.media_vault_path`. No new YAML DSL yet; this phase is Python-only.

### 2.1 New field on `ServiceContext`

**`genesis_worker/contracts/context.py`**:

```python
@dataclass(frozen=True)
class ServiceContext(PluginContext):
    media_vault_path: Path = field(default_factory=Path)
```

Defaults to `Path()` so callers that build contexts ad-hoc (tests, internal helpers) don't need to supply it. The framework always populates it in production.

The docstring gains a paragraph explaining the field — `vault_path` (model vault, both axes) vs. `media_vault_path` (services only).

### 2.2 Registry populates the field

**`genesis_worker/registries.py`** — `ServiceRegistry._context_for_name`:

```python
def _context_for_name(self, name: str) -> ServiceContext:
    p = self._settings.paths
    return ServiceContext(
        name=name,
        repo_root=p.resolved_repo_root,
        vault_path=p.resolved_vault_path,
        media_vault_path=p.resolved_media_vault_path,  # NEW
        host_info=collect_host_info(),
        secrets=self._settings.secrets.accessor(),
        options=self._settings.options_for("services", name),
        data_dir=p.data_dir / name.replace("_", "-"),
        config_dir=p.config_dir / name.replace("_", "-"),
        cache_dir=p.cache_dir / name.replace("_", "-"),
        state_dir=p.state_dir / name.replace("_", "-"),
        log_dir=p.log_dir / name.replace("_", "-"),
    )
```

`_common_kwargs` is **not** touched — the new field is service-only, so it stays in `_context_for_name` rather than the shared helper.

### 2.3 Test factory gains the kwarg

**`genesis_worker/tests/_factories.py`** — `service_ctx`:

```python
def service_ctx(
    root: Path,
    *,
    name: str = "test-service",
    vault_path: Path | None = None,
    media_vault_path: Path | None = None,  # NEW
    options: dict[str, Any] | None = None,
    secrets: SecretsAccessor | None = None,
    host_info: HostInfo | None = None,
) -> ServiceContext:
    return ServiceContext(
        name=name,
        repo_root=root,
        vault_path=vault_path if vault_path is not None else root / "vault",
        media_vault_path=media_vault_path if media_vault_path is not None else root / "media-vault",  # NEW
        options=options or {},
        host_info=host_info if host_info is not None else HostInfo.empty(),
        secrets=secrets if secrets is not None else NoSecretsAccessor(),
        **_dirs(root),
    )
```

### 2.4 Tests

**`genesis_worker/tests/test_context_vault_path.py`** — extend with a parallel file **`test_context_media_vault_path.py`**:

- `test_service_context_carries_media_vault_path` — factory with explicit `media_vault_path`; `ctx.media_vault_path` returns it.
- `test_source_context_does_not_carry_media_vault_path` — verify the asymmetry: `SourceContext` has no `media_vault_path` attribute (or, if we add it via dataclass inheritance, it's never populated by the source registry).
- `test_service_context_media_vault_path_defaults_when_unset` — factory default lands at `<root>/media-vault`.
- `test_service_registry_populates_media_vault_path_on_every_service` — every constructed service has a non-empty `ctx.media_vault_path` matching `settings.paths.resolved_media_vault_path`.
- `test_service_context_field_order_media_vault_after_vault_path` — locks the field position so an accidental reorder surfaces as a test failure. `media_vault_path` follows `vault_path` so plugin authors scanning the context find related fields together.

Phase 2 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 3 — `$media_vault_path` YAML placeholder

Make the new path available to declarative services via the same placeholder mechanism that already handles `$state_dir`, `$data_dir`, `$vault_path`.

### 3.1 Loader resolves the placeholder

**`genesis_worker/utils/services/loader.py`** — `_resolve_placeholder`:

```python
def _resolve_placeholder(
    key: str, ctx: ServiceContext, options: Mapping[str, Any], auth_token: Any
) -> Any:
    ...
    if key == "vault_path":
        return ctx.vault_path
    if key == "media_vault_path":  # NEW
        return ctx.media_vault_path
    ...
```

Single conditional. The single-placeholder string case returns the typed `Path` (stringified to its `str()` form by the surrounding `_resolve_string` helper, exactly like the existing path placeholders). Mixed strings go through the regex substitution path.

### 3.2 Tutorial update

**`docs/tutorials/declarative-services.md`** — add a row to the path-placeholder table:

```markdown
| Placeholder | Resolves to | Example (in a YAML) |
|---|---|---|
| ... | ... | ... |
| `$media_vault_path` | `<media_vault>` (default `~/media`) | `/photoprism/originals: "$media_vault_path"` |
```

### 3.3 Tests

**`genesis_worker/tests/test_yaml_loader.py`** — extend:

- `test_resolve_media_vault_path_placeholder` — declare `payload["env"] = {"MEDIA": "$media_vault_path"}`; load the spec; `svc.config.extra_env["MEDIA"] == str(ctx.media_vault_path)`.
- `test_resolve_media_vault_path_placeholder_mixed` — declare `payload["volumes"] = {"/data/media": "$media_vault_path/photoprism"}`; load the spec; `svc.config.extra_volumes["/data/media"] == f"{ctx.media_vault_path}/photoprism"`.
- `test_resolve_media_vault_path_placeholder_recursive` — declare `payload["env"] = {"MEDIA_LIST": ["$media_vault_path"]}`; load; assert the list contains the resolved string.

Phase 3 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 4 — Settings UI surface

The Settings page renders `snapshot_settings` rows. The new `media_vault_path` row appears automatically once the data layer is in place; this phase verifies the UI integration.

### 4.1 No code change to `ui/settings.py`

The page iterates `worker.snapshot_settings()` and renders one row per `SettingSnapshot`. Phase 1's facade change already feeds the new entry.

### 4.2 Tests

**`genesis_worker/tests/test_settings_ui.py`** — extend:

- `test_settings_ui_lists_media_vault_path` — snapshot includes a `paths.media_vault_path` row whose `override_key == "GENESIS_PATHS__MEDIA_VAULT_PATH"`.
- `test_settings_ui_overrides_media_vault_path` — write the override key into `user-overrides.env`; refresh; verify the snapshot reflects the new value.

Phase 4 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 5 — ComfyUI input/output migration

Flip ComfyUI's input/output defaults from `<data_dir>/data/{input,output}` to `<media_vault_path>/comfyui/{inputs,outputs}`. The bind-mount container paths stay the same; only the host side moves.

### 5.1 Defaults flip in the service constructor

**`genesis_worker/services/comfyui/service.py`**:

```python
self._data_input_dir = opts.data_input_dir or ctx.media_vault_path / "comfyui" / "inputs"
self._data_output_dir = opts.data_output_dir or ctx.media_vault_path / "comfyui" / "outputs"
```

The `data_input_dir` and `data_output_dir` options remain — operators who want the legacy layout (or a custom path) set them explicitly. Help text on the options notes the new default.

### 5.2 Lifecycle pre-creates the new subdirs

**`genesis_worker/services/comfyui/lifecycle.py`** — `start_comfyui` already pre-creates `vault_models_dir` with `mkdir(parents=True, exist_ok=True)`. Extend the same pattern to input/output dirs that are about to be bind-mounted. The two paths arrive via the existing `volumes` dict; pre-create each value that doesn't exist yet, mirroring the `vault_models_dir` treatment.

### 5.3 Tests

**`genesis_worker/tests/test_comfyui_service.py`** — update:

- `test_construction_data_dirs_default_under_data_dir` becomes `test_construction_data_dirs_default_under_media_vault`. Asserts `_data_input_dir == tmp_path / "media-vault" / "comfyui" / "inputs"` and `_data_output_dir == tmp_path / "media-vault" / "comfyui" / "outputs"`. The path comes from the factory's default `media_vault_path`.
- Existing tests that pass explicit `data_input_dir` / `data_output_dir` options stay green (they assert on the explicit value, not the default).
- New test `test_construction_data_dirs_under_media_vault_uses_ctx_path` — when `ctx.media_vault_path` is set to a non-default path, both defaults follow.

**`genesis_worker/tests/test_comfyui_lifecycle.py`** — extend with a `start_comfyui` test that asserts the input/output host dirs are `mkdir`-ed before the bind mounts reach docker.

Phase 5 gate: pytest, pyright, ruff check, ruff format --check.

## Phase 6 — PhotoPrism default uses `$media_vault_path`

Replace the photoprism YAML's `"$data_dir/.."` hack with `"$media_vault_path"`.

### 6.1 Update the YAML

**`genesis_worker/services/_declarative/photoprism.yaml`**:

```yaml
options:
  pictures_dir:
    type: path
    default: "$media_vault_path"
    ui_label: Pictures directory
    ui_help: Directory PhotoPrism indexes for photos and videos (defaults to the worker's media vault at ~/media)
    ui_group: Storage
```

### 6.2 Update the test

**`genesis_worker/tests/test_declarative_specs.py`** — `test_photoprism_identity`:

The existing assertion `originals_host.endswith("/data/..")` becomes `originals_host == str(ctx.media_vault_path)`. The hack is gone; the default resolves to the literal media vault path.

### 6.3 Update the tutorial example

**`docs/tutorials/declarative-services.md`** — the worked example for photoprism shows `pictures_dir` default as `"$media_vault_path"` (replacing the previous `"$data_dir/.."`). Update the inline text accordingly.

### 6.4 Tests

Phase 6 gate: pytest, pyright, ruff check, ruff format --check. The photoprism identity test already covers the resolved path; no new test needed.

## Phase 7 — Documentation

### 7.1 README / cross-references

If the README has a "Paths" section, add `media_vault_path` next to `vault_path`. The cross-reference is a one-line add.

### 7.2 Tutorial completion

The declarative-services tutorial example for photoprism is updated in phase 6.3. The path-placeholder table is updated in phase 3.2. Confirm both updates landed and the surrounding prose still reads correctly.

### 7.3 Cross-reference in ADR-023

**`docs/arch/adr-023-vault-path-on-plugin-context.md`** — add a one-line footnote at the end of "Why not a separate field on `ServiceContext` only?" section: `Updated by ADR-037: media_vault_path takes the opposite path — service-only, not lifted to PluginContext, because no source consumes it today.`

### 7.4 Annotate superseded hack

**`docs/arch/adr-036-user-editable-service-config.md`** — the section listing `"$data_dir/.."` as the photoprism default now references ADR-037 as the superseder. Inline annotation, not a status change.

### 7.5 AGENTS.md cross-reference

Add a one-line pointer in the AGENTS.md "Architecture" section if appropriate, noting that the media vault is the parallel concept to the model vault for service-produced content.

Phase 7 gate: pytest, pyright, ruff check, ruff format --check.

---

## Final gate

After phase 7:

```bash
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

All four must pass. End-to-end smoke: launch the worker, navigate to the Settings page, confirm `media_vault_path` shows up alongside `vault_path`, override it, refresh, confirm the snapshot reflects the new value. Confirm a freshly-loaded photoprism spec binds `/photoprism/originals` to the media vault path. Confirm ComfyUI's default input/output dirs land under the media vault.
