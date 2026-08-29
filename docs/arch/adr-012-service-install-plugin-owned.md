# ADR-012: Service install — plugin-owned acquisition with streaming progress

## Title

Service install — plugin-owned acquisition, progress-streamed, version-pinnable.

## Context

The current llama-swap deployment assumes `llama-swap` is on PATH (`bin/up`'s `command -v` check; `LlamaSwapService.is_available()` reads `shutil.which`). A fresh machine has neither the binary nor, for CUDA-enabled `llama-server`, a built one — the official llama.cpp project publishes no CUDA prebuilt; the bin/ build path is a hand-rolled CUDA compile.

The `InferenceService` ABC has no slots for acquisition. Install capability lives implicitly in the operating environment. There is no surface for the worker to fetch its own dependencies.

The model-acquire flow (`AcquireSession` returning `AcquireStep` objects) already implements the right shape — a streaming progress state machine with cancellation — but no ABC exists for service install.

Future plugins will need heterogeneous acquisition: ComfyUI is `git clone` + `pip install`, vLLM is a Python wheel, AIToolkit is a tarball. Each service should pick its own install path without changes to the framework, the same way ADR-009 lets each plugin pick its own options schema.

Observed asymmetry on the in-tree plugin: llama-swap needs two installables — a single-binary GoReleaser tarball (`mostlygeek/llama-swap`) alongside a directory-tree artifact (`ai-dock/llama.cpp-cuda`). The mechanism must not flatten the two cases.

Boundary constraints:

- ADR-008 freezes `bin/`, `Makefile`, `recipes.yaml`, `config.yaml`, and the live llama-swap process. Nothing here may regenerate `config.yaml`, edit `recipes.yaml`, or restart the running service as a routine operation.
- ADR-009 enforces the framework / plugin boundary. The install mechanism must live behind an ABC; the framework may not reach behind to peek at upstream URLs or vendor layouts.

## Decision

We will add a first-class install surface to the inference-service axis, plugin-owned, with progress streaming and version-pinning.

### Disk layout

```
<data_dir>/                                = XDG_DATA_HOME/genesis_worker/<dir_name>/
  installs/
    <name>/                                # one entry per installable (e.g. "llama-swap", "llama-server")
      <version>/                           # upstream tag, opaque
        <binary>                           # the executable, or upstream-laid-out subtree
        MANIFEST                           # url, sha256, fetched_at, source, size_bytes
      current -> <version>                 # symlink; absent when not installed

<state_dir>/                                = XDG_STATE_HOME/genesis_worker/<dir_name>/
  selections.yaml                           # { <name>: <version> }; absent entries fall back to
                                            # installs/<name>/current; missing file = all latest
```

For GoReleaser-style single-binary tarballs, `<binary>` is the executable itself. For directory-tree artifacts (like ai-dock/llama.cpp-cuda), `<binary>` is the upstream-laid-out subtree (`bin/llama-server`, `include/`, etc.) — paths inside the subtree match what upstream publishes. The `current` symlink is resolved by plugins as part of `install()` to mark the new default.

One MANIFEST sidecar per install, YAML by repo convention (ADR-006):

```yaml
name: llama-swap
version: v0.4.5
source:
  url: https://github.com/mostlygeek/llama-swap/releases/download/v0.4.5/...
sha256: <hex>
fetched_at: 2026-01-15T10:00:00Z
size_bytes: 12345678
```

### Selection resolution

Per installable, in precedence order:

1. `selections.yaml` carries `name → version`. If present and that version is installed, it wins.
2. Else, the `installs/<name>/current` symlink resolves to the highest installed version.
3. Else, the installable is `NOT_INSTALLED`.

The UI reads the resolved selection and surfaces it read-only in v1. Writing pinned versions lands in v1+1. `selections.yaml` exists if and only if pinning has happened.

### Contract surface

```python
class InstallState(StrEnum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"

class InstallVersion(NamedTuple):
    version: str
    url: str
    sha256: str | None
    size_bytes: int | None

class ServiceInstall(ABC):
    name: str
    @abstractmethod
    def state(self) -> InstallState: ...                     # disk-only, no network
    @abstractmethod
    def installed_version(self) -> str | None: ...
    @abstractmethod
    def available_versions(self) -> list[InstallVersion]: ...   # network; UI gates polling
    @abstractmethod
    def binary_path(self) -> Path | None: ...
    @abstractmethod
    def install(self, *, version: str | None = None) -> InstallSession: ...
    @abstractmethod
    def uninstall(self, *, version: str | None = None) -> None: ...
```

`ServiceCapabilities.can_install: bool` (default False). `InferenceService.installs() -> list[ServiceInstall]` defaults to `[]`.

`InstallSession` is a sibling of `AcquireSession` with the same shape (returns `AcquireStep`-shaped progress objects, has `submit(choice) / cancel()`):

- `kind` extends with `inspecting | fetching | verifying | extracting`; existing `complete | failed | cancelled` reused.
- `progress: AcquireProgress | None` carries bytes_done / bytes_total / speed_bps / eta_s during `fetching`.
- `cache_dir` and `total_bytes` retained from the model-acquire shape.
- `file_groups` unused (model-only).

The two sessions share no parent class; both produce the same dataclass. UI components written against `AcquireStep` accept either producer. Sessions expose `wait()` for synchronous use (CLI mode) and `current_step()` for polling (UI mode).

### Plugin-owned mechanisms

The framework exposes `ServiceInstall`; the plugin owns the implementation. Two are realized in v1 for llama-swap, with more expected to follow:

1. **Direct-binary release tarball.** Query upstream Releases (GitHub in v1), pick the host-architecture asset, download to a temp file under the plugin's cache dir, verify SHA256 against upstream `checksums.txt` if published, extract into `<data_dir>/installs/<name>/<version>/`, write MANIFEST, atomically update `current`.
2. **uv-tool install.** Invoke `uv tool install <package>@<version>` with `--bin-dir <data_dir>/installs/<name>/<version>/bin` and a venv location under `cache_dir`. Record MANIFEST from `uv tool list` JSON output, or by re-reading the entry-point directory.

Plugins report `can_install=True`, declare installables via `installs()`, and own the implementation. The framework reads `binary_path()` to resolve the executable at lifecycle time.

### Lifecycle binding

`lifecycle.start_swap` and friends take an absolute `binary: Path` instead of doing `shutil.which`. `LlamaSwapService.start()` resolves `self._installs[<llama-swap binary index>].binary_path()` and passes it in. `LlamaSwapService.is_available()` returns True iff the installable state is INSTALLED and `binary_path()` points at an existing file. Tests no longer monkeypatch PATH; they pass a binary path directly.

### Supply chain

A direct-binary download verifies SHA256 against a `checksums.txt` published alongside the upstream release when one is published. The MANIFEST records what was verified. If no checksums are published upstream, the MANIFEST records `sha256: null, verified: false` and the UI surfaces a documented "no upstream verification available" notice; installation still proceeds in v1.

Auth is unauthenticated in v1; GitHub's public Releases API allows 60 requests/hour/IP — sufficient for UI check-on-demand. PAT auth is deferred.

### Out of scope for v1

- `regenerate_config()`, the recipe path in `data/recipes.yaml`, anything that writes `config.yaml`. Frozen per ADR-008.
- Wiring the ai-dock `llama-server` artifact into the running llama-swap process. The binary lands in `installs/llama-server/<version>/`, but the recipe still names `vendor/llama.cpp/build/bin/llama-server`. Integration lands when recipes migrate (post-v1, ADR-008 phase 10).
- UI control to write `selections.yaml` (read-only display in v1).
- Authenticated GitHub API.

### Migration

v1 only. `bin/up` continues to work unchanged. The pre-installed llama-swap on PATH remains valid until phase 10 retires the bin/ scripts.

## Consequences

**Positive**

- A plugin installs its own dependencies; the framework doesn't grow install logic per service.
- The install protocol mirrors `AcquireSession`'s progress shape; one streaming UI component drives both.
- Version pinning lands in `selections.yaml`; the `current` symlink keeps disk reads fast.
- SHA256 verification is per-install, so re-installs don't re-download unnecessarily.

**Negative**

- New disk layout introduces `installs/` and `state/` per service plugin. v1 has nothing to migrate.
- Two on-disk views (MANIFEST sidecar + symlink) require atomic-write discipline during install. Installation is single-threaded per installable in v1; concurrent UI clicks are serialized by the worker.
- `selections.yaml` is a future extension point in v1 with no UI control. The file exists iff pinning has happened; UI displays state but can't change it.
- GitHub Releases API rate-limits unauthenticated callers. UI checks on demand and caches per session; no polling.
- A plugin whose installable lays out a directory tree must keep the path stable relative to MANIFEST. If upstream renames an entry point, the plugin's binary_path reference breaks; the fix lives in the plugin.

**Neutral**

- All new code lives entirely under XDG paths. Repo-root state files (ADR-008) are unaffected.
- ABC changes are backward-compatible additions: `can_install` defaults to False; `installs()` defaults to `[]`.
- The lifecycle binding change (`binary: Path` parameter) is internal to the llama-swap plugin; the public `InferenceService` surface is unchanged.

## Status

Accepted.


