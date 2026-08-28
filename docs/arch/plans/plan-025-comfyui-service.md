# Plan 025: ComfyUI service plugin

Implements [ADR-025](../adr-025-comfyui-service-plugin.md). Phase 3 of the ComfyUI rollout. Depends on Phase 1 ([plan-023](../plans/plan-023-vault-path-on-plugin-context.md)) and Phase 2 ([plan-024](../plans/plan-024-docker-process-utility.md)) merged.

## Working rules

- Branch: `feature/comfyui-service` off `main` (continues from Phases 1 + 2).
- Six sub-phases (3.1 through 3.6). Each sub-phase is independently mergeable from a code-correctness perspective; suggested commit per sub-phase.
- No real `docker pull` in tests.
- Verification gate at the end of each sub-phase:
  ```
  uv run pytest -q
  uv run pyright
  uv run ruff check genesis_worker
  ```
- End-of-plan manual smoke (step 3.6.2) needs a real GPU; on a CPU-only host only the install/uninstall path is exercisable.

---

## Phase 3.1 — Options, install backend, lifecycle

### Step 3.1.1 — `genesis_worker/services/comfyui/__init__.py`

```python
"""ComfyUI inference service plugin."""

from .options import ComfyUiOptions
from .service import ComfyUiService

__all__ = ["ComfyUiOptions", "ComfyUiService"]
```

### Step 3.1.2 — `genesis_worker/services/comfyui/options.py`

`ComfyUiOptions(BaseModel)` per ADR-025 Decision: *Options*. Defaults evaluated at construction:

- `data_python_dir`, `data_custom_nodes_dir`, `data_input_dir`, `data_output_dir`, `data_profiles_dir` default to `<ctx.data_dir>/comfyui/data/<name>`.
- `vault_models_dir` defaults to `<ctx.vault_path>/comfyui`.
- `symlinks_file` defaults to `<ctx.config_dir>/comfyui/model_symlink.yaml`.
- `puid`/`pgid` default to `os.getuid()`/`os.getgid()` when `None` — auto-derived from the host user so the container matches.

The `ComfyUiService` constructor evaluates defaults against `ctx`; `options.py` defines the schema only.

### Step 3.1.3 — `genesis_worker/services/comfyui/install.py`

`ComfyUiImage(ServiceInstall)` + `_DockerPullInstallSession(BackgroundInstallSession)`.

- `name = "comfyui-cuda"` (mirrors the upstream image name).
- `state()` → `INSTALLED` iff `DockerContainer.image_present(f"{repo}:{tag}")` else `NOT_INSTALLED`.
- `installed_version()` → the local tag (from `list_local_tags`) matching the resolved selection. Selection lives under `<state_dir>/.../current` symlink, mirroring `InstallLayout.resolved_selection()`.
- `available_versions()` → `list_remote_tags(repo)` mapped to `InstallVersion(version=tag, url=f"ghcr.io/{repo}:{tag}", sha256=None, size_bytes=None)`.
- `binary_path()` → `None`. (The service's `is_available()` is overridden to consult `state()`.)
- `install(version)` → returns a `_DockerPullInstallSession(image=..., session=self)`.
- `uninstall()` → `docker rmi <image>:<tag>` for the selected tag. The service's `uninstall_installable` guard refuses if the container is running.

`_DockerPullInstallSession._run_inner()`:

```python
def _run_inner(self) -> None:
    self._publish(AcquireStep(kind="fetching", title=f"pulling {self._image}"))
    DockerContainer.pull(
        self._image,
        progress=self._on_progress,
        cancel=self._cancel.is_set,
    )
    self._publish(AcquireStep(kind="complete", title=f"pulled {self._image}"))

def _on_progress(self, line: str) -> None:
    if self._cancel.is_set():
        raise _Canceled
    self._publish(AcquireStep(kind="fetching", title=line))
```

Version cache: `<cache_dir>/releases-cache/comfyui-cuda.json`, shape `{version: 1, fetched_at: float, releases: [tag, ...]}`, 15-min TTL. Mirrors `GithubReleaseTarball._read_release_cache`.

### Step 3.1.4 — `genesis_worker/services/comfyui/lifecycle.py`

Thin wrappers over `DockerContainer` + `HealthProbe`. No tmux.

```python
def start_comfyui(svc, *, gpu_required_check=True) -> StartResult:
    if gpu_required_check and not DockerContainer.image_present(svc.image_ref):
        return StartResult(ok=False, message=f"image not pulled: {svc.image_ref}")
    container = DockerContainer(svc.container_name)
    container.remove()
    return container.run(
        image=svc.image_ref,
        command=svc.extra_args,
        ports={f"{svc.listen_port}/tcp": (svc.listen_host_addr, svc.listen_port)},
        volumes={
            "/opt/comfyui/python": svc.data_python_dir,
            "/opt/comfyui/app/custom_nodes": svc.data_custom_nodes_dir,
            "/opt/comfyui/app/input": svc.data_input_dir,
            "/opt/comfyui/app/output": svc.data_output_dir,
            "/opt/comfyui/app/user": svc.data_profiles_dir,
            "/opt/comfyui/app/models": svc.vault_models_dir,
        },
        env={"PUID": str(svc.puid), "PGID": str(svc.pgid)},
        runtime=svc.runtime if svc.gpu_required and DockerContainer.nvidia_runtime_available() else None,
        gpu_flags=[f"driver={svc.gpu_driver}", f"count={svc.gpu_count}"] if (svc.gpu_required and DockerContainer.nvidia_runtime_available()) else None,
        hostname=svc.container_name,
        restart=svc.restart_policy,
    )

def stop_comfyui(svc, *, timeout_s=30) -> StopResult:
    container = DockerContainer(svc.container_name)
    result = container.stop(timeout_s=timeout_s)
    container.remove()
    return result

def status_comfyui(svc) -> ServiceStatus:
    container = DockerContainer(svc.container_name)
    endpoint = f"http://{HealthProbe.resolve_connect_host(svc.listen_host)}:{svc.listen_port}/"
    if not container.is_running():
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    probe = HealthProbe(svc.listen_host, svc.listen_port, probe_path="/")
    if probe.probe():
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)

def wait_ready_comfyui(svc, timeout_s: float) -> bool:
    return HealthProbe(svc.listen_host, svc.listen_port, probe_path="/").wait_ready(timeout_s)
```

GPU probing happens at the service layer (not here); this module assumes preconditions.

### Step 3.1.5 — Tests for 3.1

- `genesis_worker/tests/test_comfyui_options.py` — defaults, overrides, PUID/PGID auto-detection (mock `os.getuid`/`os.getgid`).
- `genesis_worker/tests/test_comfyui_install.py` — version cache read/write/pull; pull session progress + cancellation. Mock `DockerContainer.pull` and the cache.
- `genesis_worker/tests/test_comfyui_lifecycle.py` — start/stop/status dispatch. Mock `DockerContainer` and `HealthProbe`.

### Step 3.1.6 — Run gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

Commit Phase 3.1.

---

## Phase 3.2 — Symlink applier

### Step 3.2.1 — `genesis_worker/services/comfyui/symlinks.py`

`SymlinkApplier` per ADR-025. Reads `<config_dir>/comfyui/model_symlink.yaml`; falls back to an empty file if missing. Resolves catalog entries via a callable injected at construction (avoids the applier reaching into the framework).

```python
@dataclass(frozen=True)
class SymlinkRow:
    source: str
    entry: str
    piece: str
    target_subdir: str
    symlink_path: Path
    target_path: Path | None   # None when dangling


@dataclass(frozen=True)
class ApplyResult:
    created: list[SymlinkRow]
    updated: list[SymlinkRow]
    errors: list[tuple[SymlinkRow, str]]


@dataclass(frozen=True)
class PruneResult:
    removed: list[SymlinkRow]


class SymlinkApplier:
    def __init__(
        self,
        *,
        symlinks_file: Path,
        vault_models_dir: Path,
        catalog: Callable[[], Catalog],
    ) -> None: ...

    def apply(self) -> ApplyResult: ...
    def prune_dangling(self) -> PruneResult: ...
    def list_current(self) -> list[SymlinkRow]: ...

    def add(self, rows: list[SymlinkRow]) -> None: ...
    def remove(self, rows: list[SymlinkRow]) -> None: ...
```

YAML schema (read + written by the applier):

```yaml
version: 1
symlinks:
  - source: huggingface
    entry: "Qwen/Qwen-Image"
    piece: qwen_image_bf16.safetensors
    target_subdir: diffusion_models
```

Apply algorithm:

1. Read yaml.
2. For each row: lookup `catalog.by_source()[row.source]`; find entry by `row.entry`; find piece by `row.piece` (matches `piece.filename`); resolve target path (`piece.path`).
3. Ensure `<vault>/comfyui/<target_subdir>/` exists.
4. Resolve target symlink path: `<vault>/comfyui/<target_subdir>/<basename(piece)>`.
5. If a regular file exists at the symlink path → record error `TargetNotSymlink`; do not clobber.
6. If a symlink exists pointing elsewhere → replace.
7. If a symlink exists pointing to the correct target → no-op.
8. Otherwise create.

Prune algorithm:

1. Walk `<vault>/comfyui/>` recursively.
2. For each symlink, check `os.path.exists(symlink)`; if dangling → record for removal.
3. Build updated yaml by removing rows whose `symlink_path` matches a removed symlink.
4. Atomic write: read → modify → write.
5. Return counts.

### Step 3.2.2 — Tests for 3.2

`genesis_worker/tests/test_comfyui_symlinks.py`:

- `apply_creates_symlinks_from_yaml` — fixture catalog with three entries; yaml lists two; assert two symlinks created with correct targets.
- `apply_handles_missing_catalog_entry` — yaml entry whose catalog entry has been deleted; applier records `EntryNotFound` error.
- `apply_refuses_to_clobber_regular_file` — pre-create a regular file at the target path; applier records `TargetNotSymlink` and does not delete it.
- `apply_replaces_wrong_target_symlink` — pre-create a symlink pointing elsewhere; applier replaces it.
- `apply_is_idempotent` — running apply twice produces the same filesystem state and the same `ApplyResult` (zero updated/created on the second run).
- `prune_removes_dangling_symlinks` — two valid + one dangling; prune removes the dangling entry from both filesystem and yaml.
- `prune_preserves_user_owned_symlinks` — symlink not in yaml pointing to an existing file; prune leaves it alone.
- `list_current_reflects_yaml_and_disk` — yaml has 2 rows; disk has 1 valid + 1 dangling + 1 user-owned; `list_current` reports the 2 yaml rows, dangling row has `target_path=None`, user-owned symlink is not listed.

### Step 3.2.3 — Run gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

Commit Phase 3.2.

---

## Phase 3.3 — Service

### Step 3.3.1 — `genesis_worker/services/comfyui/service.py`

`ComfyUiService(InferenceService)` per ADR-025.

Constructor:

- Reads `ComfyUiOptions` from `ctx.options`.
- Caches `has_nvidia_gpu: bool` from `subprocess.run(["nvidia-smi", "-L"], timeout=5, ...)`. Pattern from llama-swap's variant detection.
- Initialises `ComfyUiImage` installable (data_dir, cache_dir, state_dir, secrets from ctx).
- Resolves all bind-mount paths, symlink file path, etc.

Required overrides:

- `is_available()` → `self._install.state() == InstallState.INSTALLED`. **Inversion of the cptr/llama-swap convention** (no host binary); comment explains why.
- `capabilities()` → `can_generate_config=False, can_export_for_agent=False, can_serve_llm=False, can_serve_image=True, can_train_models=False, has_web_ui=True, can_install=True`.
- `resource_estimate()` → `vram_bytes_typical=12e9, vram_bytes_min=6e9, cpu_cores_recommended=4`.
- `is_running()` → `lifecycle.is_running_comfyui(self)` (or directly check container).
- `runtime_endpoint()` → `None`.
- `web_ui_endpoint()` → `f"http://{public_host}:{listen_port}/"` when running.
- `start()` → guard `has_nvidia_gpu` when `gpu_required=True`; otherwise call `lifecycle.start_comfyui(self)`.
- `stop()` → `lifecycle.stop_comfyui(self)`.
- `status()` → `lifecycle.status_comfyui(self)`.
- `wait_ready(timeout_s)` → `lifecycle.wait_ready_comfyui(self, timeout_s)`.
- `tail_log(n_bytes=8192)` → `DockerContainer(self._options.container_name).logs(tail_lines=max(50, n_bytes // 80))`. (Docker has no byte-count flag; convert roughly. Lines ≈ bytes ÷ 80.)
- `public_host()` → `socket.gethostname()` fallback chain like cptr.
- `installs()` → `[self._install]`.
- `primary_installable()` → `self._install`.
- `uninstall_installable(name, *, version=None)` → refuse if running, delegate to installable.
- `ui_pages` → three pages with explicit `url_path` values (`comfyui_status`, `comfyui_image`, `comfyui_models`) so Streamlit's slug inference doesn't collide.

### Step 3.3.2 — Tests for 3.3

`genesis_worker/tests/test_comfyui_service.py`:

- Construction: defaults applied, options honored, `puid`/`pgid` auto-default when None.
- `capabilities()` matches ADR-025.
- `is_available()` returns `False` when state==NOT_INSTALLED, `True` when INSTALLED (mock installable's `state()`).
- `is_available()` does NOT consult `binary_path()` — comment in the test explains the inversion.
- `is_running`/`start`/`stop`/`status`/`wait_ready` dispatch to lifecycle (mock lifecycle functions).
- `tail_log()` calls `DockerContainer.logs()` with the right `tail_lines` (mock `DockerContainer`).
- `public_host()` resolves correctly under `public_host=None` (mock `socket.gethostname`).
- `web_ui_endpoint()` returns URL when running, `None` when stopped.
- `runtime_endpoint()` returns `None`.
- `uninstall_installable()` refuses when running.
- `ui_pages` returns three pages with explicit `url_path` values.

### Step 3.3.3 — Run gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

Commit Phase 3.3.

---

## Phase 3.4 — UI pages

### Step 3.4.1 — `genesis_worker/services/comfyui/ui/__init__.py`

Empty (package marker; pages discovered via `Path(__file__).parent`).

### Step 3.4.2 — `genesis_worker/services/comfyui/ui/status.py`

Mirror cptr's status page. Use `render_service_controls` and `render_tail_log` (ADR-013 utilities).

Additions:

- Container info panel with image ref, container name, listen address, public URL, GPU detection state.
- Disabled Start with explanatory caption when `gpu_required=True` and `has_nvidia_gpu=False`. Mirror the install-button disable pattern from `_install_flow.py`.

### Step 3.4.3 — `genesis_worker/services/comfyui/ui/image.py`

Mirror llama-swap's Binaries page (`genesis_worker/services/llama_swap/ui/binaries.py`) one-for-one:

- One installable expander (not four).
- Disabled install when `gpu_required=True` and no GPU.
- Inline install progress via `@st.fragment(run_every="2s")` against `session.current_step()`.

The shape and widget keys reuse the Binaries-page conventions: `_SESSION_KEY_PREFIX`, `_drop_pending_prefix`, `_render_step`, etc.

### Step 3.4.4 — `genesis_worker/services/comfyui/ui/models.py`

Symlink management page. Sections:

- Header row with `Add symlinks` button (opens `@st.dialog("Add symlinks")`) and `Prune dangling` button.
- Symlink table from `SymlinkApplier.list_current()`: columns = Source, Entry, Piece, Target subdir, Resolved path, Actions (delete).
- Add dialog (multi-step):
  1. Multi-select source names (defaults to all sources).
  2. Multi-select entries (filtered to those with at least one piece whose filename matches `WEIGHT_EXTS`).
  3. For each selected entry, list pieces with checkboxes; per checked piece, a role dropdown (`checkpoints`, `diffusion_models`, `loras`, `vae`, `controlnet`, `t2i_adapter`, `clip`, `unet`, `style_models`, `upscale_models`).
  4. Submit → `applier.add(rows)` + `applier.apply()`; `st.rerun()`.
- Prune action: confirm checkbox + button, calls `applier.prune_dangling()`, displays count.

### Step 3.4.5 — Tests for 3.4

UI pages are not unit-tested directly (existing convention). The Models page's logic — yaml read/write + apply — is covered by the symlink applier tests (Phase 3.2). A single import-smoke test catches syntax errors:

- `genesis_worker/tests/test_comfyui_ui_imports.py` — assert each of `status.py`, `image.py`, `models.py` imports without error.

### Step 3.4.6 — Run gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

Commit Phase 3.4.

---

## Phase 3.5 — Auto-discovery

### Step 3.5.1 — Verify auto-discovery

Extend `genesis_worker/tests/test_services_registry.py`:

```python
def test_comfyui_service_is_discovered():
    reg = ServiceRegistry(Settings())
    names = [s.name for s in reg.all()]
    assert "comfyui" in names
```

### Step 3.5.2 — Plugin boundary

The plugin-boundary test (`test_plugin_imports_only_allowed_surfaces`) walks `services/` recursively. Run it with the new ComfyUI files present. All ComfyUI modules must import only from `genesis_worker.contracts` and `genesis_worker.utils`.

### Step 3.5.3 — Run gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

Commit Phase 3.5.

---

## Phase 3.6 — Documentation and final verification

### Step 3.6.1 — Update README

Add a "ComfyUI service" section:

- Image source: `ghcr.io/genesis-scaffolding/comfyui-cuda`.
- Default port `8188`; override via `GENESIS_SERVICES__COMFYUI__LISTEN_PORT`.
- Bind-mount layout (one vault path + five data-dir paths).
- Symlink workflow (Models page).
- GPU requirement and the auto-derived PUID/PGID.
- Symlink gotchas from ADR-025 (ownership, staleness, snapshot rotation, cross-filesystem).

### Step 3.6.2 — Manual smoke (requires GPU; non-CI)

```bash
uv run streamlit run genesis_worker/ui/app.py
# or
uv run genesis-worker-ui
```

Smoke checklist:

1. Status page renders. Image is not installed → install badge.
2. Image page lists remote tags (or empty list if GHCR rate-limited — verify separately via `gh api /orgs/genesis-scaffolding/packages/container/comfyui-cuda`).
3. Install `v0.34.0-cuda-13.0-amd64`. Watch progress. Confirm `docker images | grep comfyui` shows the tag.
4. Back on Status, Start. Confirm `docker ps | grep comfyui` shows the container with the six bind mounts.
5. From another terminal: `curl -s http://localhost:8188/` returns ComfyUI's HTML.
6. Models page: pick an HF catalog entry that has a safetensor piece; target subdir `checkpoints`. Submit. Confirm the symlink appears at `<vault>/comfyui/checkpoints/<basename>`.
7. From inside the container: `docker exec comfyui ls /opt/comfyui/app/models/checkpoints/` shows the symlink target.
8. Prune dangling: doesn't find anything yet (symlink target exists).
9. Stop. Container stops cleanly. `docker ps | grep comfyui` is empty.

On a CPU-only host, only steps 1–3 are exercisable (Start button blocked by GPU guard).

### Step 3.6.3 — Final gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

All must pass. Commit Phase 3.6 and merge locally on `main` after user approval:

```bash
git checkout main
git merge --no-ff feature/comfyui-service
```

---

## Files changed summary

| File | Change |
|---|---|
| `genesis_worker/services/comfyui/__init__.py` | Create |
| `genesis_worker/services/comfyui/options.py` | Create |
| `genesis_worker/services/comfyui/install.py` | Create |
| `genesis_worker/services/comfyui/lifecycle.py` | Create |
| `genesis_worker/services/comfyui/symlinks.py` | Create |
| `genesis_worker/services/comfyui/service.py` | Create |
| `genesis_worker/services/comfyui/ui/__init__.py` | Create (empty) |
| `genesis_worker/services/comfyui/ui/status.py` | Create |
| `genesis_worker/services/comfyui/ui/image.py` | Create |
| `genesis_worker/services/comfyui/ui/models.py` | Create |
| `genesis_worker/tests/test_comfyui_options.py` | Create |
| `genesis_worker/tests/test_comfyui_install.py` | Create |
| `genesis_worker/tests/test_comfyui_lifecycle.py` | Create |
| `genesis_worker/tests/test_comfyui_symlinks.py` | Create |
| `genesis_worker/tests/test_comfyui_service.py` | Create |
| `genesis_worker/tests/test_comfyui_ui_imports.py` | Create |
| `genesis_worker/tests/test_services_registry.py` | Extend |
| `README.md` | Document ComfyUI service |

## Notes

- **GPU detection is duplicated** with llama-swap's variant detection. A `TODO(host-info)` comment in `service.py` points at the future "framework collects host info" direction. No extraction in this plan.
- **`is_available()` inversion is documented in the test**, not the contract. The contract's `is_available()` semantics ("is the service ready to start?") remain valid; only the implementation source changes.
- **The symlink applier doesn't watch the filesystem.** A user-deleted symlink is reported as dangling on `list_current`; the next `apply()` re-creates it from the yaml. A user-deleted target makes the row dangling; prune cleans up.
- **`tail_log()` byte-to-line conversion.** Docker's `logs --tail` is line-based; `tail_log(n_bytes=8192)` converts to ~100 lines as an approximation. The contract uses bytes; the conversion is internal.
- **Phases 1 and 2 are prerequisites.** Plan-025 should not start until plan-023 and plan-024 have merged. Each phase has its own commit.
