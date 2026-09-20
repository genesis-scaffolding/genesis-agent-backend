# ADR-037: Media vault — framework-managed root for user-produced content

## Title

Media vault — `PathsSettings.media_vault_path` / `ServiceContext.media_vault_path` / `$media_vault_path` placeholder, parallel to the model vault but for content produced by services (ComfyUI inputs/outputs, photos indexed by PhotoPrism, etc.).

## Status

Accepted.

## Context

The fleet is growing beyond inference. ComfyUI generates images; PhotoPrism, Jellyfin, Immich, Navidrome, and similar services organise user content; future peers will produce other media. Today these services share user content through **ad-hoc paths that bend the existing concepts out of shape**:

- ComfyUI mounts its inputs and outputs at `<data_dir>/data/{input,output}` — deep under the worker's per-service data dir, where no other service can see them without poking at another service's directory.
- The photoprism declarative YAML defaults `pictures_dir` to `"$data_dir/.."` — the literal parent of the worker's data dir, a hack to reach the directory above XDG and find user-supplied pictures. The substitution produces strings like `~/.local/share/genesis-worker/..`, which docker then resolves to `~/.local/share/`, an even worse outcome because it indexes the *state root*, not the user's media.
- A future service that wants to share content with PhotoPrism has no clean way to point at "the media directory" without reaching into a sibling service's data dir or another service's options.

The model vault (`vault_path`) is the right precedent. It's a framework-resolved root, lifted onto `PluginContext` (ADR-023), with a per-source `vault_subdir` attribute that the registry uses to derive each source's `local_path`. The semantics are clean: sources acquire into the vault; the rest of the worker reads from it. We're missing the **output half of the loop** — services producing content into a shared, framework-managed root that other services can mount and index.

### Forces

- **Framework/plugin boundary (ADR-009).** Plugins may only import from `genesis_worker.contracts` and `genesis_worker.utils`. Any new path must be resolved by the framework and handed to the plugin on its context — never reached for from `os.environ` or a settings file by the plugin.
- **Symmetry with `vault_path`.** The existing path concept is well-defined; the new one should follow the same shape (XDG-style base + optional explicit override + framework-resolved root + plugin context field + `$placeholder` substitution). Diverging from the precedent means two mental models for "vault-like" paths.
- **The user wants clean separation.** They explicitly chose `~/media` as the default over `<xdg-data>/genesis-worker/media-vault` — keeping user-produced content visibly outside the worker's internal XDG state (`<xdg-data>/genesis-worker/`). This is a deliberate UX choice: power users can `ls ~/media` to see what their services have produced; the XDG layout stays opaque.
- **No source uses it today.** Sources walk the model vault (acquired content). The media vault is output-only. Lifting it to `PluginContext` for symmetry would be YAGNI; `ServiceContext` is sufficient and easy to lift later if a source ever needs it (the same path ADR-023 took for `vault_path`).
- **Backward compat for ComfyUI.** Existing installations have data at `<data_dir>/data/{input,output}`. The migration is a default flip; no auto-move. Users keep their data and opt-in to the new layout by setting `data_input_dir` / `data_output_dir` explicitly. Documentation makes the choice explicit.
- **The photoprism `$data_dir/..` hack is genuinely broken.** Even before this ADR, photoprism's default was reaching outside the per-service data dir into the XDG state root. The new default (`$media_vault_path`) is both cleaner and safer.

## Decision

We will add a `media_vault_path` concept to the framework, parallel to the existing `vault_path` but scoped to services.

### 1. New field on `PathsSettings`

**`genesis_worker/settings.py`**:

```python
class PathsSettings(BaseModel):
    data_dir: Path = ...
    config_dir: Path = ...
    cache_dir: Path = ...
    state_dir: Path = ...
    log_dir: Path = ...

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

**Default location: `~/media`.** Explicitly chosen over `<xdg-data>/genesis-worker/media-vault` to keep user content visibly separate from worker internals. The model vault's `~/models` default predates the XDG discipline (it survives as backward compat via `MODELS_ROOT`); the new path follows the same precedent — visible, predictable, easy to inspect. Override is always available via `GENESIS_PATHS__MEDIA_VAULT_PATH`.

The `Media_vault_path` field is optional; `resolved_media_vault_path` is the always-defined accessor the rest of the framework reads. Same shape as `vault_path` / `resolved_vault_path`.

### 2. New field on `ServiceContext` (services only)

**`genesis_worker/contracts/context.py`**:

```python
@dataclass(frozen=True)
class ServiceContext(PluginContext):
    media_vault_path: Path = field(default_factory=Path)
```

The field defaults to `Path()` so callers that construct a `ServiceContext` without it (tests, ad-hoc Python) continue to compile. The framework always populates it.

**Why not `PluginContext`?** ADR-023 lifted `vault_path` to `PluginContext` because services needed it but only sources had used it before. Here, **no source uses the media vault today** — sources walk acquired content, not user-produced content. Sources could plausibly index media later (a "stable-diffusion-outputs" source that walks `~/media/comfyui/outputs` for cataloging), but that's hypothetical. Lifting later is a one-line ADR if the need materialises; the precedent is set.

### 3. Registry populates `media_vault_path`

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

`_common_kwargs` is **not** touched — `media_vault_path` is service-only, so it stays in `_context_for_name` (not the shared helper).

### 4. `$media_vault_path` placeholder for declarative services

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

A single conditional, mirroring `$vault_path`. The placeholder works in `env` values, `volumes` values, `auth.token_file`, and `pre_start_hooks[].target` — the same scope as the existing path placeholders. Resolution is a single substitution; mixed strings are stringified via `str(Path)`.

### 5. ComfyUI defaults flip to the media vault

**`genesis_worker/services/comfyui/service.py`**:

```python
self._data_input_dir  = opts.data_input_dir  or ctx.media_vault_path / "comfyui" / "inputs"
self._data_output_dir = opts.data_output_dir or ctx.media_vault_path / "comfyui" / "outputs"
```

The bind mounts in `start()` stay the same (the container sees `/opt/comfyui/app/{input,output}`); only the host path moves from `<data_dir>/data/{input,output}` to `<media_vault_path>/comfyui/{inputs,outputs}`.

**Migration is a default flip, not an auto-move.** Existing installations keep data at `<data_dir>/data/{input,output}`; that path is never touched. The `data_input_dir` / `data_output_dir` options remain, so a user who wants the legacy behaviour sets them explicitly. New installations land at the new path.

`_ensure_volume_dirs` already pre-creates bind-mount targets as the host user; the new subdirs come along for the ride because the lifecycle's `start_comfyui` pre-creates `vault_models_dir`. We extend that pattern to also `mkdir` the input/output dirs if they're going to be bind-mounted.

### 6. PhotoPrism default uses `$media_vault_path`

**`genesis_worker/services/_declarative/photoprism.yaml`**:

```yaml
options:
  pictures_dir:
    type: path
    default: "$media_vault_path"
    ui_label: Pictures directory
    ui_help: Directory PhotoPrism indexes for photos and videos (the worker's media vault)
    ui_group: Storage
```

The `"$data_dir/.."` hack disappears. The default now resolves to `<resolved_media_vault_path>`, which is `~/media` by default — the user's media lives where they expect to find it. The volume declaration stays `volumes: { /photoprism/originals: "$options.pictures_dir" }`.

### 7. Settings page surfaces the new knob

**`genesis_worker/facade.py`** — `snapshot_settings` gains a `media_vault_path` row, and `_merge_paths_with_overrides` learns the new key. The Settings page (`ui/settings.py`) auto-renders rows from `snapshot_settings`, so no UI changes are needed beyond the data layer.

`.env.example` gains a commented `GENESIS_PATHS__MEDIA_VAULT_PATH` block alongside the existing `GENESIS_PATHS__VAULT_PATH` block.

### Why not a per-service `media_vault_subdir` attribute?

The model vault has `vault_subdir` as a class attribute on `ModelSource`. We considered the parallel: an `InferenceService.media_vault_subdir` attribute (Python + YAML) plus a `$media_vault` placeholder that resolves to `<media_vault_path>/<subdir>`. The cost-benefit doesn't pay: services already pick their own subdir by hand today (`ctx.vault_path / "comfyui"`), the subdir is trivially derivable (`<media_vault_path>/<name>`), and the placeholder form is just as compact. If we later find services picking inconsistent names we can introduce a convention; today there's no signal that one is needed.

### Why `~/media` and not `<xdg-data>/genesis-worker/media-vault`?

The user explicitly chose `~/media`. Two reasons made this the right call:

1. **Visibility.** User-produced content deserves to be findable. `ls ~/media` shows what the worker has produced; `ls ~/.local/share/genesis-worker/` shows the worker's internals (cache, state, service data). Mixing the two makes the worker's XDG layout opaque to the user.
2. **Precedent.** The model vault's `~/models` default predates the XDG discipline and survives as backward compat via `MODELS_ROOT`. The user wants the new concept to follow the same visible-and-findable precedent, not the newer XDG-collocation pattern. Override is always available.

## Plan

`docs/arch/plans/plan-037-media-vault-path.md` — seven-phase execution, each phase independently committable, ending with the full test/type/lint gate.

## Consequences

**Positive**

- Services that produce user content have a clear, framework-managed home (`~/media/<service>/<role>` by default) that other services can mount and index without ad-hoc paths.
- The photoprism `$data_dir/..` hack is gone; the default now points at the media vault root, which is what the operator means by "my pictures directory".
- The Settings page gets a `media_vault_path` knob in the same row pattern as `vault_path`. Operators who want a different location (e.g. a NAS mount) set it once.
- The `$media_vault_path` placeholder is available to every declarative YAML service; no per-service plumbing.
- The boundary holds: the framework resolves and hands the path in via `ctx.media_vault_path`; services reach for nothing themselves.

**Negative**

- ComfyUI's default input/output path changes. Existing installations keep their data at the old path but new binds will use the new path only when the user opts in via `data_input_dir`/`data_output_dir` overrides. This is a one-line setting for the operator who cares; documented in the option help text. The risk is a user who installs the new version without reading the changelog and wonders why their container starts with empty inputs/outputs — mitigated by the explicit "Default points at the media vault" help text.
- `ServiceContext` is one field wider. Test factories and any ad-hoc context construction site need to know about `media_vault_path`. The default value (`Path()`) keeps backward compat for callers that don't supply it; the framework always populates it in production.
- A future source that walks user-produced content would need `media_vault_path` on `SourceContext` too. Lifting it to `PluginContext` later is a one-ADR change; we accept the asymmetry today in exchange for YAGNI.
- The `media_vault_path` default is `~/media`, which collides with the convention some desktop environments use for media (KDE's purpose, GNOME's `xdg-user-dirs`). On machines where the user has set `XDG_VIDEOS_DIR` / `XDG_PICTURES_DIR` etc., `~/media` is an unrelated directory; the operator can override. We document this in `.env.example`.

**Neutral**

- `Media_vault_path` doesn't auto-create its root directory on startup. The XDG paths don't either (ADR-004). Services that own a subdir `mkdir` it at start time, mirroring the existing pattern with `vault_models_dir`.
- No new option type in the YAML DSL. `$media_vault_path` is a path placeholder, same scope as `$vault_path`. The `path` / `port` / `env_map` / `mount_map` taxonomy is unchanged.
- `Media_vault_path` is a new keyword in `Settings` (under `paths.media_vault_path`). Existing user-overrides files that don't mention it continue to work; the default applies.
- The Settings page rendering is unchanged — `snapshot_settings` is data-driven, so adding a row is one new entry.
