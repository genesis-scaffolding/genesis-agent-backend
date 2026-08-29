# ADR-028: Retire install runtime; move specialized sessions to utils

## Context

`BackgroundSession` (ADR-027) is the runtime base for any thread-driven session: daemon worker, cancel event, log tail, terminal-state translation, `wait()`, `_start()`. `HfAcquireSession` already uses it.

The install side has the same runtime needs but never got the same refactor. Today there are two parallel runtime bases:

- `BackgroundSession(AcquireSession)` — the new base, used by HF acquire.
- `BackgroundInstallSession(InstallSession)` — the older base, ~80 lines, in `genesis_worker/utils/install/session.py`. Implements the same runtime machinery inline (thread, cancel, `_Canceled` → `cancelled`, exception → `failed`).

Three services implement installables and each carries its own private session class:

| Service | Local session class | File |
|---|---|---|
| llama-swap | `_GithubReleaseInstallSession` | `services/llama_swap/installs.py` |
| comfyui | `_DockerPullInstallSession` | `services/comfyui/install.py` |
| cptr | `_UvToolInstallSession` | `services/cptr/install.py` |

Each is a thin wrapper around a different backend: GitHub release tarball, `docker pull --progress=json`, `uv tool install`. The work they do is reusable — nothing llama-swap, comfyui, or cptr specific. They just live in the service plugin because that's where the runtime base class lived.

Pressure:
- The install runtime is duplicated logic — same thread/cancel/terminal pattern, two implementations.
- Adding a new install flavor (apt, snap, conda) means either copying `_GithubReleaseInstallSession` into another service, or another subclass of `BackgroundInstallSession` that nobody else can reuse.
- The `InstallSession` contract and `BackgroundInstallSession` runtime can be retired; their job is now done by `AcquireSession` + `BackgroundSession`.

`ServiceInstall` (the plugin-side ABC — `state()`, `installed_version()`, `available_versions()`, `install()`, `uninstall()`) is **not** in scope. It's the metadata interface services use to expose installables. It stays.

## Decision

We will retire the install-side runtime (`InstallSession`, `BackgroundInstallSession`) and move the three specialized session classes into `utils/acquire/` where they can be reused. Services configure them; `utils/` provides the runtime.

### 1. New files in `genesis_worker/utils/acquire/`

- **`github_release.py`** — `GithubReleaseAcquireSession(BackgroundSession)`. Streams a GitHub release asset: query releases, download with progress, verify SHA, extract, write manifest, update symlink. Takes `repo_owner`, `repo_name`, `version`, `layout`, `cache_root`, `asset_filter`, `binary_rel`, `checksums_url`, `secrets` as configuration. Moved from `services/llama_swap/installs.py`.
- **`docker_pull.py`** — `DockerPullAcquireSession(BackgroundSession)`. Streams `docker pull --progress=json`, parses with `DockerPullProgress`, publishes per-line progress. Takes `image`, `on_complete` as configuration. Moved from `services/comfyui/install.py`.
- **`uv_tool.py`** — `UvToolAcquireSession(BackgroundSession)`. Runs `uv tool install <spec>` and publishes `fetching` → `complete` (or `failed`). Takes `package_name`, `version` as configuration. Moved from `services/cptr/install.py`.

`__init__.py` re-exports the three classes.

### 2. `_Canceled` moves to `utils/background_session.py`

It's the runtime's exception (raised by `_run_inner` to signal cancellation). Currently in `utils/install/session.py`. After that file is gone, `_Canceled` lives with `BackgroundSession`.

### 3. `contracts/install.py` slimmed

- **`InstallSession` ABC removed.** Service installables return `AcquireSession` from the unified contract.
- **`ServiceInstall.install()` return type changes** from `InstallSession` to `AcquireSession`.
- **`InstallState` enum stays.** Pre-acquire metadata (`NOT_INSTALLED` / `INSTALLED`).
- **`InstallVersion` NamedTuple stays.** Pre-acquire version metadata (`version`, `url`, `sha256`, `size_bytes`).
- `ServiceInstall` ABC stays otherwise unchanged.

### 4. Services slim down

Each service's `install.py`/`installs.py` loses its private session class. `install()` now imports and instantiates the appropriate utility session with plugin-specific configuration.

**`services/llama_swap/installs.py`:**
- Keep `LlamaSwapBinary`, `LlamaServerCUDA`, `LlamaServerCPU`, `LlamaServerVulkan`, `_UpstreamLlamaServerBinary` (all `ServiceInstall` subclasses).
- Keep `_asset_name_matches_linux_amd64_tarball`, `_ai_dock_llama_cpp_cuda_asset`, `_upstream_llama_cpu_asset`, `_upstream_llama_vulkan_asset` (plugin-private asset filters).
- Keep `GithubReleaseTarball` (the GitHub release API client — version listing, asset matching). It's a service-private helper, not a runtime.
- Remove `_GithubReleaseInstallSession`. Its body becomes `GithubReleaseAcquireSession` in `utils/`.
- Each `ServiceInstall.install()` calls `GithubReleaseAcquireSession(...)` with the right config.

**`services/comfyui/install.py`:**
- Keep `ComfyUiImage(ServiceInstall)`, `_normalise_host_arch`, `detect_host_arch`, `_matches_host_arch`, `_cache_path`, `_read_cache`, `_write_cache` (image tag listing, arch filtering, caching — all plugin-private).
- Remove `_DockerPullInstallSession`. Its body becomes `DockerPullAcquireSession` in `utils/`.
- `ComfyUiImage.install()` calls `DockerPullAcquireSession(...)`.

**`services/cptr/install.py`:**
- Keep `CptrInstall(ServiceInstall)`, `_uv_tool_installed_version`, `_http_get_json` (uv/PyPI helpers).
- Remove `_UvToolInstallSession`. Its body becomes `UvToolAcquireSession` in `utils/`.
- `CptrInstall.install()` calls `UvToolAcquireSession(...)`.

### 5. UI helper updates

`utils/ui/_install_flow.py` (`render_inline_install`) currently uses `InstallSession.current_step()`. After the migration it operates on `AcquireSession` — call `view()` instead.

### 6. Tests update

`tests/test_cptr_install.py` and `tests/test_comfyui_install.py` currently exercise the install sessions end-to-end. After:

- Tests instantiate the utility-level sessions directly (e.g. `GithubReleaseAcquireSession(...)`), not the service-private ones.
- `session.current_step()` → `session.view()`.
- `session.submit(...)` is now void (already the case for HF acquire); tests that asserted on the return value get adjusted.
- `session.wait()` is unchanged (already in the contract).
- New tests for the three utility classes can live alongside the existing install tests, or in a new `tests/test_acquire_utils.py` if preferred.

### 7. Out of scope

- **`ServiceInstall` is not retired.** It's the plugin-side entry point for "what's installed, what's available, how to install." Only the runtime (what `install()` returns) changes.
- **`InstallState` / `InstallVersion` / `InstallLayout` / `Manifest`** keep their names. They're pre-acquire metadata, not session runtime.
- **`AcquireFileGroup` / `HfAcquireState` / `HfAcquireView` / `HfAcquireChoice`** are unchanged.
- **Generalizing asset filters** (e.g. consolidating `_asset_name_matches_linux_amd64_tarball` etc.) is out of scope. They stay as plugin-private helpers.
- **A new generic `AcquireTarget` plugin interface** is out of scope. `ServiceInstall` and `ModelSource` continue to be the two plugin interfaces.

## Status

Proposed.

## Consequences

**Positive:**

- One runtime base (`BackgroundSession`) for all long-running sessions. No more duplication of thread / cancel / terminal-state logic.
- The three install flavors become reusable utilities. A new service that wants to install a binary from a GitHub release just configures `GithubReleaseAcquireSession` — no copying 100 lines of session code.
- All session-related code is unified under the "acquire" vocabulary. `InstallSession` and `BackgroundInstallSession` are gone.
- `_Canceled` lives next to `BackgroundSession`, which is where it's raised and caught.
- `ServiceInstall` keeps its role as the plugin-side metadata interface. The change is purely runtime-side.

**Negative:**

- `ServiceInstall.install()` returns `AcquireSession` (the unified type). Plugin authors who read the type signature might expect "InstallSession" semantics. The rename forces them to learn the unified contract.
- The three utility sessions need clear configuration interfaces. The asset-filter callbacks, SHA verification, layout paths — all parameter shapes need to be designed once and reused. Slight upfront design cost.
- `BackgroundInstallSession` disappears; any external code depending on it (none in the codebase, but possibly in downstream forks) breaks.
- Tests reorganize: install-session tests now exercise utility classes directly. Test names and structure shift.

**Neutral:**

- `ServiceInstall` still uses `InstallState` and `InstallVersion` — pre-acquire metadata, not the session runtime. The names stay.
- Plugin-private helpers (`_asset_name_matches_linux_amd64_tarball`, `_normalise_host_arch`, `_uv_tool_installed_version`, etc.) stay where they are.
- `InstallLayout` and `Manifest` (in `utils/install/`) stay. They're physical install layout, not session runtime.

## Verification

- `uv run pytest -q` passes.
- `uv run pyright` clean (or only pre-existing errors on `tests/test_docker_container.py:616`).
- `uv run ruff check genesis_worker` clean (or only pre-existing errors in `recipes_view.py` and `tests/test_docker_container.py`).
- Manual UI smoke: walk through install of one binary (llama-swap), one docker image (comfyui), one uv tool (cptr). Verify progress, cancel, and terminal states all work.
