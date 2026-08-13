# Spec 005: Service install

## Goal

Implement ADR-012. Add a first-class install surface to the inference-service axis for the llama-swap plugin: two installables (`llama-swap` binary, `llama-server` directory tree), plugin-owned acquisition, progress streaming via the existing `AcquireStep` dataclass, version selection via state-dir YAML + on-disk `current` symlink. The running llama-swap and config-generation pipeline are not touched.

## Disk layout

Concretely: `<data_dir>` and `<state_dir>` for the `llama_swap` plugin resolve through ADR-004's XDG machinery to `XDG_DATA_HOME/genesis_worker/llama-swap/` and `XDG_STATE_HOME/genesis_worker/llama-swap/`.

```
<data_dir>/
  installs/
    llama-swap/                          # one entry per ServiceInstall
      v0.4.5/
        llama-swap                       # the executable, chmod +x
        MANIFEST                         # yaml sidecar; schema below
      v0.4.4/
        llama-swap
        MANIFEST
      current -> v0.4.5                  # "latest installed"; absent when not installed
    llama-server/
      b4500/
        bin/llama-server                 # mirror upstream's tree
        include/                         # whatever upstream shipped
        MANIFEST
      current -> b4500

<state_dir>/
  selections.yaml                        # optional; absent = "always latest"
```

The `installs/<name>/` directory uses the `ServiceInstall.name` (not the upstream project name) so the layout is predictable. The MANIFEST captures the upstream source separately.

## Manifest schema

YAML per repo convention (ADR-006). Lives at the root of each `<version>/` directory. Round-tripped with `yaml.safe_load` / `yaml.safe_dump`.

```yaml
name: llama-swap
version: v0.4.5
source:
  url: https://github.com/mostlygeek/llama-swap/releases/download/v0.4.5/llama-swap_Linux_x86_64.tar.gz
sha256: <hex>           # null when upstream publishes no checksums
verified: true          # false when the upstream had no checksums.txt to verify against
fetched_at: 2026-01-15T10:00:00Z
size_bytes: 12345678
install_method: github_release_tarball
```

## Selections schema

`selections.yaml`:

```yaml
llama-swap: v0.4.4
llama-server: b4500
```

Read-only in v1; UI displays the resolved selection but does not write. Pin-write UX is deferred. Resolution precedence per installable:

1. `selections.yaml` entry — that exact version (must be installed; otherwise fall through to step 2 and treat as "user-pinned but missing").
2. `installs/<name>/current` symlink — highest installed version.
3. `NOT_INSTALLED`.

## Contract surface

New module `genesis_worker/contracts/install.py`. Re-exported through `genesis_worker/contracts/__init__.py`.

New module `genesis_worker/contracts/secret.py`: `SecretsAccessor` ABC and `NoSecretsAccessor` default. Plugins ask the framework for a secret by name; the framework resolves it from `Settings.secrets`. The plugin never reaches into `os.environ` or `.env` directly (ADR-009). `PluginContext` adds a `secrets: SecretsAccessor` field, defaulted to `NoSecretsAccessor()`.

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
    def state(self) -> InstallState: ...                              # disk-only
    @abstractmethod
    def installed_version(self) -> str | None: ...
    @abstractmethod
    def available_versions(self) -> list[InstallVersion]: ...         # network; UI gates polling
    @abstractmethod
    def binary_path(self) -> Path | None: ...
    @abstractmethod
    def install(self, *, version: str | None = None) -> InstallSession: ...
    @abstractmethod
    def uninstall(self, *, version: str | None = None) -> None: ...

class InstallSession(ABC):
    @abstractmethod
    def current_step(self) -> AcquireStep: ...
    @abstractmethod
    def submit(self, choice: AcquireChoice) -> AcquireStep: ...
    @abstractmethod
    def cancel(self) -> None: ...
    @abstractmethod
    def wait(self) -> AcquireStep: ...
```

`ServiceCapabilities` gains `can_install: bool = False`. `InferenceService` gains `installs() -> list[ServiceInstall]` returning `[]`.

`InstallSession` produces `AcquireStep` (existing dataclass, no field changes) with extended `kind` strings:

- `fetching` — bytes streaming; `progress=AcquireProgress(bytes_done, bytes_total, speed_bps, eta_s)` and `cache_dir` set.
- `verifying` — sha256 check.
- `extracting` — archive unpack.
- `complete`, `failed`, `cancelled` — terminal states, reused from model-acquire.

`AcquireSession` and `InstallSession` share no parent class. Both produce `AcquireStep`. UI components iterate on that dataclass regardless of producer.

## Lifecycle binding

`genesis_worker/services/llama_swap/lifecycle.py` signature change:

```python
def start_swap(
    binary: Path,                       # NEW; absolute path
    config: Path,
    listen_addr: str,
    session_name: str,
    log_file: Path,
    health_timeout_s: float = _DEFAULT_HEALTH_TIMEOUT_S,
) -> StartResult:
    if not binary.is_file():
        return StartResult(ok=False, message=f"binary not found: {binary}")
    ...
    cmd = f"{shlex.quote(binary)} --config {shlex.quote(config)} -listen {listen_addr} -watch-config 2>&1 | tee -a {shlex.quote(log_file)}"
```

Path lookups via `shutil.which` are gone. Validation moves to module entry. `LlamaSwapService.start()` resolves the binary from the installable; `is_available()` becomes `self._llama_swap_install.binary_path() is not None`.

## Two installables for llama-swap

`genesis_worker/services/llama_swap/installs.py`:

### LlamaSwapBinary — release-tarball backend

Sources from `mostlygeek/llama-swap` Releases. Asset pattern: `{tool}_Linux_{arch}.tar.gz`. `binary_path()` resolves to the symlink target's `llama-swap`.

### LlamaServerBinary — directory-tree backend

Sources from `ai-dock/llama.cpp-cuda` Releases. Asset pattern: `llama.cpp-cuda-*.tar.gz` (verified at design time; recorded as a class constant). `binary_path()` resolves to `<current>/bin/llama-server`.

Both compose a shared `GithubReleaseTarball` backend in `genesis_worker/services/llama_swap/installs.py`. The helper takes the install's `installs_root`, `cache_root`, an asset matcher, an optional checksums URL, and yields an `InstallSession` whose `current_step()` streams progress through `AcquireStep` directly.

> **Note on location:** the backend lives inside the plugin package, not `genesis_worker/utils/install/`, because it produces `AcquireStep` objects from `genesis_worker.contracts` — and `utils` is a leaf package per ADR-009. The pure helpers (`Manifest`, `InstallLayout`) stay in `utils/install/`; only the framework-aware backend moves.

### Out of v1: wiring `LlamaServerBinary` into the running llama-swap

The recipe still names `vendor/llama.cpp/build/bin/llama-server`. The ai-dock artifact lands in `installs/llama-server/<version>/` but is unwired. Integration lands when recipes migrate (post-v1, ADR-008 phase 10).

## UI

Two views:

1. **Status page** (`genesis_worker/services/llama_swap/ui/status.py`) — new "Binaries" section between Service info and Configuration. One row per installable: state badge, resolved selection, "Manage binaries →" deep-link. Same pattern as "Manage config →".

2. **New Binaries page** (`genesis_worker/services/llama_swap/ui/binaries.py`) — one expander per installable. Top: state + resolved selection. Body: version picker (from `available_versions()`), `Install` / `Reinstall` / `Uninstall` / `Cancel` buttons. When an install is in flight, an `@st.fragment(run_every="2s")` renders the session's `current_step()`. The fragment is the same component used on the Status page console (and on the Acquire page) — one renderer, three producers.

The page is registered in `service.LlamaSwapService.ui_pages` between Status and Config editor.

## Verification conditions

| # | Test | File |
|---|------|------|
| 1 | Manifest round-trip and optional-field handling | `tests/test_manifest.py` |
| 2 | Disk layout: empty / single / multiple / pinned / fall-through behavior | `tests/test_install_layout.py` |
| 3 | Tarball backend against a local fake GitHub server (success, sha256 mismatch, cancel mid-fetch) | `tests/test_install_tarball.py` |
| 4 | Lifecycle takes `binary: Path`; rejects missing path before any tmux activity; PATH monkeypatch removed | `tests/test_lifecycle.py` (extend) |
| 5 | `LlamaSwapService.installs()` returns the two installables; `is_available()` reflects install state; `start()` rejects before install | `tests/test_service_llama_swap.py` (extend) |
| 6 | Streamlit fragment renders end-state and progress for at least one install path | manual smoke |
| 7 | `tests/test_plugin_boundary.py` confirms `services/llama_swap/installs.py` and `ui/binaries.py` import only `contracts`, `utils`, and the plugin's own package | (extend if needed for `utils/install/*`) |

All seven pass on `uv run pytest -q` and gates in `uv run ruff check genesis_worker` and `uv run pyright` exit clean. The running llama-swap on `:8080` continues to serve throughout validation.

## Out of scope for v1

- Recipe changes (`data/recipes.yaml`).
- `regenerate_config()` changes.
- UI control to write `selections.yaml`.
- GitHub PAT auth.
- Symlink portability to non-Linux (worker runs on Linux only today).
- `OUTDATED` as an `InstallState` value — UI derives it from `installed_version()` vs `available_versions()`.

## Files changed

```
genesis_worker/
  contracts/
    install.py                          NEW   InstallState, InstallVersion, ServiceInstall, InstallSession
    service.py                          EDIT  can_install field; installs() default
    __init__.py                         EDIT  re-export
  utils/
    install/
      __init__.py                       NEW
      manifest.py                       NEW   Manifest dataclass + parse/dump
      layout.py                         NEW   InstallLayout: installs/ + state/ paths, current symlink, selections.yaml
  services/llama_swap/
    installs.py                         NEW   LlamaSwapBinary, LlamaServerBinary
    lifecycle.py                        EDIT  binary: Path parameter, shlex.quote
    service.py                          EDIT  installs(), is_available(), start() resolves binary path; can_install
    ui/
      binaries.py                       NEW   Binaries page
      status.py                         EDIT  compact Binaries section
genesis_worker/tests/
  test_manifest.py                      NEW
  test_install_layout.py                NEW
  test_install_tarball.py               NEW
  test_lifecycle.py                     EDIT
  test_service_llama_swap.py            EDIT
  test_plugin_boundary.py               EDIT (only if a new cross-plugin import sneaks in)
```
