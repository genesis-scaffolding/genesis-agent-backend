# ADR-025: ComfyUI service plugin

## Title

ComfyUI as an `InferenceService` plugin — Docker-backed image generation service with symlink-managed model binding into the vault.

## Status

Accepted. Depends on ADR-023 (`vault_path` on `PluginContext`) and ADR-024 (`DockerContainer` utility).

## Context

The fleet's two existing inference services are llama-swap (LLM serving, config-driven) and cptr (web-UI proxy). Neither runs as a container. ComfyUI does — the official image is `ghcr.io/genesis-scaffolding/comfyui-cuda:v0.34.0-cuda-13.0-amd64`, GPU-bound via the NVIDIA Container Toolkit, and expects a persistent `/opt/comfyui/{app/models,app/custom_nodes,app/input,app/output,app/user,python}` tree across container restarts.

The user-facing question ComfyUI raises that llama-swap does not is: *where do the models live?* llama-swap reads a generated config that names binaries on disk; ComfyUI reads a directory tree (`models/<role>/<file>`). Our vault is organised by source (`huggingface/hub/models--org--repo/snapshots/<sha>/...`) — fundamentally incompatible.

Three integration shapes were considered:

1. **`extra_model_paths.yaml` driven from catalog.** Extend `classify()` with ComfyUI roles; the service bind-mounts the vault root read-only; `regenerate_config()` writes `extra_model_paths.yaml` mapping catalog entries to ComfyUI roles. ComfyUI natively honours the file. No symlinks; ComfyUI reads files directly from the vault.
2. **A dedicated `comfyui` source.** New `ModelSource` walking `<vault>/comfyui/models/<role>/`; the service bind-mounts only that subdir.
3. **Symlink farm with explicit user-managed YAML.** The service bind-mounts `<vault>/comfyui/` as the ComfyUI models dir. The user (via UI) maintains a `model_symlink.yaml` declaring `(catalog entry, piece, target_subdir)` triples. An applier writes the corresponding symlinks under the bind mount. ComfyUI inside the container reads the symlinked files transparently.

We chose (3). Rationale:

- (1) leans on ComfyUI's `extra_model_paths` config. The config is re-read at container start, so the framework would need to write the file before every `docker run`. Workable but adds a generated-config axis (`can_generate_config=True`) that is conceptually different from llama-swap's config — the user has no reason to edit it, and the framework cannot auto-derive the ComfyUI role with confidence (a safetensor's role is encoded in its filename by convention; classifier accuracy is unverified).
- (2) splits the model collection across two mental locations — most ComfyUI models are on HuggingFace, so users would acquire via the HF source and then copy into the ComfyUI source's directory. Two source-axis hops for one model is friction.
- (3) keeps the user's mental model simple: the vault contains the real files; `<vault>/comfyui/<role>/` is a managed view of the vault for ComfyUI. The YAML is user-editable but UI-scaffolded (multi-select piece picker + role dropdown). The applier is the only writer of symlinks, so dangling-link cleanup is centralised.

We also chose to manage ComfyUI's persistent state (the `python` venv, custom nodes, input/output, profiles) under `<data_dir>/comfyui/data/<name>/` rather than under the vault. The vault is for content the user curates (model artifacts); ComfyUI's working state is service runtime, which the framework's XDG layout owns. Bind-mounting only `<vault>/comfyui/` for the models dir is one mount; the other five are separate mounts under the data dir.

Docker itself is reached via the new `DockerContainer` utility (ADR-024). We do not wrap `docker run` in tmux; the container is the long-running process.

## Decision

### Plugin package

```
genesis_worker/services/comfyui/
  __init__.py        # re-exports ComfyUiService, ComfyUiOptions
  options.py         # ComfyUiOptions pydantic model
  install.py         # ComfyUiImage ServiceInstall + BackgroundInstallSession subclass
  lifecycle.py       # start/stop/status via DockerContainer + HealthProbe
  symlinks.py        # SymlinkApplier — read yaml, resolve catalog, create symlinks
  service.py         # ComfyUiService(InferenceService)
  ui/
    status.py        # landing page
    image.py         # install / uninstall / version picker (mirrors llama_swap Binaries)
    models.py        # symlink management
```

Discovered automatically by `ServiceRegistry` (ADR-009). No settings-layer changes — `ComfyUiOptions` parses `ctx.options` like the other services.

### Capabilities

```python
ServiceCapabilities(
    can_generate_config=False,    # symlinks are user-managed via the Models UI, not auto-generated
    can_export_for_agent=False,   # no OpenAI-compatible API
    can_serve_llm=False,
    can_serve_image=True,
    can_train_models=False,
    has_web_ui=True,              # ComfyUI ships its own web UI on the listen port
    can_install=True,
)
```

### Options (`options.py`)

```python
class ComfyUiOptions(BaseModel):
    # --- networking ---
    listen_host: str = "0.0.0.0"
    listen_port: int = 8188        # ComfyUI default
    public_host: str | None = None

    # --- image ---
    image_repo: str = "ghcr.io/genesis-scaffolding/comfyui-cuda"
    image_tag: str = "v0.34.0-cuda-13.0-amd64"
    # Override the full pull ref via env var:
    #   GENESIS_SERVICES__COMFYUI__IMAGE = ghcr.io/.../comfyui-cuda:v0.35.0-...

    # --- container identity ---
    container_name: str = "comfyui"
    health_timeout_s: float = 90.0
    log_file: Path | None = None

    # --- runtime / GPU ---
    gpu_required: bool = True
    runtime: str = "nvidia"
    gpu_driver: str = "nvidia"
    gpu_count: str = "1"           # str so options can encode "all"
    restart_policy: str = "unless-stopped"
    # PUID/PGID auto-default to id -u / id -g at construction.
    puid: int | None = None
    pgid: int | None = None

    # --- bind mounts (host paths; defaults derive from ctx.data_dir) ---
    data_python_dir: Path | None = None         # <data_dir>/comfyui/data/python
    data_custom_nodes_dir: Path | None = None   # <data_dir>/comfyui/data/custom_nodes
    data_input_dir: Path | None = None          # <data_dir>/comfyui/data/input
    data_output_dir: Path | None = None         # <data_dir>/comfyui/data/output
    data_profiles_dir: Path | None = None       # <data_dir>/comfyui/data/user
    vault_models_dir: Path | None = None        # <vault>/comfyui

    # --- symlinks ---
    symlinks_file: Path | None = None           # <config_dir>/comfyui/model_symlink.yaml

    # --- extra container args ---
    extra_args: list[str] = ["--verbose"]      # default mirrors the compose; override as needed
```

Defaults are evaluated at construction; the six bind-mount defaults are derived from `ctx` (`data_dir` for the five; `ctx.vault_path / "comfyui"` for the models dir).

### Install backend (`install.py`)

`ComfyUiImage(ServiceInstall)`:

- `name = "comfyui-cuda"` — mirrors the upstream image name so the Binaries-style UI page reads naturally.
- `state()` → `INSTALLED` if `DockerContainer.image_present(f"{repo}:{tag}")` else `NOT_INSTALLED`.
- `installed_version()` → the local tag (from `list_local_tags(repo)`) that matches the resolved selection. The "current" tag is stored as a symlink under `<state_dir>/.../current` (mirrors `InstallLayout.resolved_selection()`).
- `available_versions()` → `list_remote_tags(repo)`; each returned as `InstallVersion(version=tag, url=f"ghcr.io/{repo}:{tag}", sha256=None, size_bytes=None)`. SHA256/size lookup would require a per-tag manifest fetch — deferred.
- `binary_path()` → `None`. There is no host binary; `is_available()` is overridden in the service to consult `state()` directly (see Decision: *is_available override* below).
- `install(version)` → `_DockerPullInstallSession` subclass of `BackgroundInstallSession` (ADR-013) that calls `DockerContainer.pull(image)` with line-by-line progress forwarded as `AcquireStep(kind="fetching", ..., title=line)`.
- `uninstall()` → `docker rmi <image>:<tag>` for the selected tag. Refuses if the container is running (same guard as `uninstall_installable` on llama-swap/cptr).

Version cache mirrors `GithubReleaseTarball`: 15 min on disk under `<cache_dir>/releases-cache/comfyui-cuda.json`.

### Lifecycle (`lifecycle.py`)

Thin wrapper over `DockerContainer` and `HealthProbe`. No tmux.

```python
def start(svc, *, image_present_check: bool = True) -> StartResult:
    if image_present_check and not DockerContainer.image_present(svc.image_ref):
        return StartResult(ok=False, message=f"image not pulled: {svc.image_ref}")
    container = DockerContainer(svc.container_name)
    container.remove()  # idempotent: remove any prior container of the same name
    ports = {f"{svc.listen_port}/tcp": (svc.listen_host_addr, svc.listen_port)}
    volumes = {
        "/opt/comfyui/python": svc.data_python_dir,
        "/opt/comfyui/app/custom_nodes": svc.data_custom_nodes_dir,
        "/opt/comfyui/app/input": svc.data_input_dir,
        "/opt/comfyui/app/output": svc.data_output_dir,
        "/opt/comfyui/app/user": svc.data_profiles_dir,
        "/opt/comfyui/app/models": svc.vault_models_dir,
    }
    env = {"PUID": str(svc.puid), "PGID": str(svc.pgid)}
    runtime = svc.runtime if svc.gpu_required and DockerContainer.nvidia_runtime_available() else None
    gpu_flags = [f"driver={svc.gpu_driver}", f"count={svc.gpu_count}"] if runtime else None
    return container.run(
        image=svc.image_ref,
        command=svc.extra_args,
        ports=ports, volumes=volumes, env=env,
        runtime=runtime, gpu_flags=gpu_flags,
        hostname=svc.container_name,
        restart=svc.restart_policy,
    )

def stop(svc, *, timeout_s=30) -> StopResult:
    container = DockerContainer(svc.container_name)
    result = container.stop(timeout_s=timeout_s)
    container.remove()
    return result

def status(svc) -> ServiceStatus:
    container = DockerContainer(svc.container_name)
    endpoint = f"http://{HealthProbe.resolve_connect_host(svc.listen_host)}:{svc.listen_port}/"
    if not container.is_running():
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    probe = HealthProbe(svc.listen_host, svc.listen_port, probe_path="/")
    if probe.probe():
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)
```

GPU probe: at construction time, the service caches `has_nvidia_gpu: bool = (subprocess.run(["nvidia-smi", "-L"], ...) succeeded)`. If `gpu_required=True` and no GPU is present, `start()` returns `StartResult(ok=False, message="no NVIDIA GPU detected; set gpu_required=false to skip")` and the Status UI greys out the Start button.

### Symlink applier (`symlinks.py`)

```python
class SymlinkApplier:
    def __init__(self, *, symlinks_file: Path, vault_models_dir: Path,
                 catalog: Callable[[], Catalog]) -> None: ...

    def apply(self) -> ApplyResult:
        """Read yaml, resolve catalog entries, create/update symlinks. Idempotent."""
        ...

    def prune_dangling(self) -> PruneResult:
        """Walk vault_models_dir, remove any symlink whose target is missing.
        Also removes the corresponding entry from the yaml. Returns counts."""
        ...

    def list_current(self) -> list[SymlinkRow]:
        """Read yaml + resolve to current on-disk state for the UI."""
        ...
```

YAML schema (lives at `<config_dir>/comfyui/model_symlink.yaml`):

```yaml
version: 1
symlinks:
  - source: huggingface
    entry: "Qwen/Qwen-Image"
    piece: qwen_image_bf16.safetensors
    target_subdir: diffusion_models
  - source: huggingface
    entry: "Wan-AI/Wan2.1-T2V-14B"
    piece: wan2.1_vae.safetensors
    target_subdir: vae
```

Identity is catalog-relative (`source`, `entry`, `piece` filename), not absolute blob path — so a snapshot rotation on HF does not invalidate symlinks. Resolution at apply time looks up `catalog().by_source()[source]`, finds the entry by `name`, finds the piece by `filename`.

Symlinks live at `<vault>/comfyui/<target_subdir>/<basename(piece)>`. The applier:

- Creates `target_subdir/` if missing.
- If a regular file exists at the target path, refuses and logs (does not clobber user data).
- If a symlink exists pointing to the wrong target, replaces it.
- If a symlink exists pointing to the correct target, no-op.

Prune scans the entire tree under `<vault>/comfyui/` (not just yaml entries) so it cleans up orphaned symlinks the yaml doesn't know about. For each dangling symlink it removes the filesystem entry and adds a row removal to a yaml update, applied atomically (read → modify → write).

### UI pages (`ui/`)

**Status** — mirrors cptr's Status page. Adds:

- Container info panel: image ref, container name, listen address, public URL, GPU detection state.
- Disabled Start button + explanatory caption when `gpu_required=True` and `has_nvidia_gpu=False`.

**Image** — mirrors llama-swap's Binaries page one-for-one (per `genesis_worker/services/llama_swap/ui/binaries.py`):

- Per-installable expander with version dropdown, Install/Reinstall, Uninstall, Refresh versions.
- Inline install progress shows during pull (`@st.fragment(run_every="2s")` against `session.current_step()`).
- Disabled install button when `gpu_required=True` and no GPU.

**Models** — new page, no precedent:

- Header with "Add symlinks" and "Prune dangling" buttons.
- Symlink table: columns = Source entry, Piece filename, Target subdir, Resolved host path, Actions (delete).
- "Add symlinks" opens a Streamlit dialog (`@st.dialog`):
  1. Pick catalog source(s) (multi-select, defaults to all).
  2. Pick entries (filtered to those with at least one piece matching `WEIGHT_EXTS`).
  3. For each entry, see pieces; check the ones to symlink and pick a target subdir per piece (dropdown of ComfyUI role names: `checkpoints`, `diffusion_models`, `loras`, `vae`, `controlnet`, `t2i_adapter`, `clip`, `unet`, `style_models`, `upscale_models`).
  4. Submit → applier writes yaml, runs `apply()`, table re-renders.
- "Prune dangling" → confirms, calls `prune_dangling()`, reports count.

### `is_available` override

```python
def is_available(self) -> bool:
    # Override: no host binary for a container service. Availability is "image pulled".
    return self._install.state() == InstallState.INSTALLED
```

This breaks the llama-swap/cptr convention where `is_available = binary_path() is not None`. The spec note: the convention exists because every other service has a single host binary to invoke. Container services invert the relationship — the host doesn't invoke anything; the container does. The contract's `is_available` semantics ("is the service ready to start?") remain valid; only the implementation source changes.

### Resource estimate

```python
ServiceResourceEstimate(
    vram_bytes_typical=12_000_000_000,    # SDXL-ish; ComfyUI is unbounded, this is a floor
    vram_bytes_min=6_000_000_000,
    cpu_cores_recommended=4,
)
```

### Symlink safety considerations (in this ADR, not deferred)

The bind-mount + symlink pattern has four gotchas the spec must address (the plan calls them out):

1. **File ownership.** The container runs with `PUID/PGID`. Defaults auto-derive from `id -u`/`id -g` so the container matches the host user. Override via `puid`/`pgid` options for multi-user hosts.
2. **Stale symlinks.** Symlinks become dangling when `worker.delete_model()` removes the source. The Models UI's "Prune dangling" button handles cleanup.
3. **Cross-snapshot rotation.** The yaml stores catalog-relative identity (`source`, `entry`, `piece` filename), not resolved blob paths. Snapshot rotations that keep the file name intact don't break the symlink.
4. **Cross-filesystem traversal.** If the vault spans multiple disks, symlinks crossing filesystems work but may be slow on networked filesystems. Documented in the service README; not addressed in v1.

## Consequences

**Positive**

- ComfyUI joins the worker as a first-class service, with the same UI conventions (Status page + role-specific extra pages) and the same install/lifecycle plumbing as llama-swap and cptr.
- The user manages their ComfyUI model collection through the existing vault (acquire via HF source) and points ComfyUI at the right artifacts via a declarative yaml. No copying, no symlink maintenance by hand.
- The bind mount that matters most (the models dir) lives under the vault, where the user's curated content lives. The other five state dirs live under XDG data, where service runtime state belongs.
- The contract change in ADR-023 is small and benefits any future container service.

**Negative**

- The symlink approach requires the user to declare mappings, but the UI dialog hides most of the friction (catalog-side multi-select, role-side dropdown).
- The Models UI page is the only one of its kind — no precedent to mirror. More design risk on UX.
- `can_generate_config=False` rules out any future auto-classification of pieces into ComfyUI roles. If we later want to suggest roles, that's a v2 capability.

**Neutral**

- Three UI pages (Status, Image, Models) is more than cptr (1) but fewer than llama-swap (5). Asymmetric, but each page earns its place.
- GPU probing in the service is a temporary measure; ADR-025 plans to fold host info into the framework (out of scope here). The probing is robust enough (the `nvidia-smi -L` timeout pattern already used by llama-swap's variant detection) that swapping it later is mechanical.
- `DockerContainer` is a thin wrapper. Future container services (A1111, Fooocus, Kohya) will compose it; they will each write their own `lifecycle.py` with their own bind-mount and arg shape. That's the right level of abstraction — the bind mount is service-specific.


