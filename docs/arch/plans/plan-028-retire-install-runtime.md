# Plan: ADR-028 — Retire install runtime; move specialized sessions to utils

Steps for executing ADR-028. Each step is independently committable; the natural commit boundary is at the end of each numbered section.

## Step 1 — Move `_Canceled` to `utils/background_session.py`

`_Canceled` is raised inside `_run_inner` to signal cancellation. It belongs next to the runtime that catches it.

**Files:**
- `genesis_worker/utils/background_session.py` — define `_Canceled` here; remove the import from `utils.install.session`.
- `genesis_worker/utils/install/session.py` — remove `_Canceled` (still has `BackgroundInstallSession` for now; gets removed in step 4).

**Verification:** `uv run pytest -q` still passes.

## Step 2 — Create `utils/acquire/` with three session classes

New directory `genesis_worker/utils/acquire/` with one file per session. Each class subclasses `BackgroundSession`.

**Files:**
- `genesis_worker/utils/acquire/__init__.py` — re-exports.
- `genesis_worker/utils/acquire/github_release.py` — `GithubReleaseAcquireSession(BackgroundSession)`. Body adapted from `_GithubReleaseInstallSession` (currently in `services/llama_swap/installs.py`).
- `genesis_worker/utils/acquire/docker_pull.py` — `DockerPullAcquireSession(BackgroundSession)`. Body adapted from `_DockerPullInstallSession` (currently in `services/comfyui/install.py`).
- `genesis_worker/utils/acquire/uv_tool.py` — `UvToolAcquireSession(BackgroundSession)`. Body adapted from `_UvToolInstallSession` (currently in `services/cptr/install.py`).

**Adaptation notes:**
- Each session takes a richer constructor with explicit config (release info, image ref, package name + version, layout, cache root, asset filter callback, etc.).
- Use `AcquireState` with `kind=AcquireStateKind.FETCHING` (initial state) — no more custom `_SessionState`.
- Use `_append_log()` (inherited from base) for log lines.
- `view()` builds `AcquireView` with the right fields for the current state.
- `_run_inner()` does the work; raises `_Canceled` or any other exception; supervisor translates.
- `submit()` is a no-op for these pipelines (no interactive steps).

**Commit:** `refactor: add acquire session utilities for binary, docker, uv`

**Verification:** pytest passes (existing tests don't import these yet, so they shouldn't break).

## Step 3 — Slim `contracts/install.py`

Retire `InstallSession`; change `ServiceInstall.install()` return type to `AcquireSession`.

**Files:**
- `genesis_worker/contracts/install.py` — remove `InstallSession` ABC. `ServiceInstall.install()` returns `AcquireSession`.

**Commit:** `refactor: retire InstallSession; ServiceInstall returns AcquireSession`

**Verification:** pyright will report errors in service plugins that import `InstallSession`. That's expected — fixed in step 4.

## Step 4 — Update services to use utility sessions

Each service loses its private session class and calls the appropriate utility session from `install()`.

**Files:**
- `genesis_worker/services/llama_swap/installs.py`:
  - Remove `_GithubReleaseInstallSession` class.
  - Each `ServiceInstall.install()` (LlamaSwapBinary, LlamaServerCUDA, LlamaServerCPU, LlamaServerVulkan) returns `GithubReleaseAcquireSession(...)` with the right config.
  - Keep `GithubReleaseTarball`, asset-filter helpers, `_UpstreamLlamaServerBinary`, `_resolve_binary` (helpers, not runtime).
- `genesis_worker/services/comfyui/install.py`:
  - Remove `_DockerPullInstallSession` class.
  - `ComfyUiImage.install()` returns `DockerPullAcquireSession(...)`.
  - Keep arch detection, cache helpers, `_ARCH_SUFFIXES`, etc.
- `genesis_worker/services/cptr/install.py`:
  - Remove `_UvToolInstallSession` class.
  - `CptrInstall.install()` returns `UvToolAcquireSession(...)`.
  - Keep `_uv_tool_installed_version`, `_http_get_json`, PyPI helpers.

**Commit:** `refactor(services): use utils acquire sessions for install flavors`

**Verification:** pytest passes (test updates come in step 6).

## Step 5 — Update UI helper

`utils/ui/_install_flow.py` operates on `InstallSession` today. After the migration, it's `AcquireSession`.

**Files:**
- `genesis_worker/utils/ui/_install_flow.py`:
  - Type annotations: `InstallSession` → `AcquireSession`.
  - `session.current_step()` → `session.view()`.
  - `session.submit(...)` returns `None` now; no longer need to capture a return value.

**Commit:** `refactor(ui): install flow operates on AcquireSession`

## Step 6 — Update tests

- `genesis_worker/tests/test_cptr_install.py` — instantiate `UvToolAcquireSession` directly; `current_step()` → `view()`.
- `genesis_worker/tests/test_comfyui_install.py` — instantiate `DockerPullAcquireSession` directly; `current_step()` → `view()`.
- `genesis_worker/tests/test_github_release_install.py` (NEW) — tests for `GithubReleaseAcquireSession` (currently tested transitively via llama-swap; pull out into a focused unit test).
- `genesis_worker/tests/test_acquire_utils.py` (NEW, optional) — shared test for any utility-level session if desired.

**Commit:** `test: update install tests for utils acquire sessions`

## Step 7 — Final cleanup

- `genesis_worker/utils/install/__init__.py` — drop `_Canceled` and `BackgroundInstallSession` from `__all__`. The file may stay as a package for `InstallLayout` / `Manifest` re-exports, or be inlined into `layout.py`/`manifest.py`.
- Verify `InstallSession` is not imported anywhere.
- Verify `_Canceled` is only imported from `genesis_worker.utils.background_session`.

**Commit:** `chore: remove BackgroundInstallSession; finalize install runtime`

## Step 8 — Gates

Run:
- `uv run pytest -q` — all pass (or only pre-existing skip).
- `uv run pyright` — clean (or only pre-existing `tests/test_docker_container.py:616`).
- `uv run ruff check genesis_worker` — clean (or only pre-existing errors in `recipes_view.py` and `tests/test_docker_container.py`).

UI smoke (manual):
- llama-swap: install `llama-swap`, verify FETCHING → COMPLETE with progress bar and log tail.
- comfyui: pull `comfyui-cuda` image, verify FETCHING → COMPLETE with docker pull progress.
- cptr: install `cptr` via uv, verify FETCHING → COMPLETE.
- Cancel mid-flight on any of the above, verify CANCELLED terminal state.

## Commit strategy

Each numbered step ends with a single commit (one-line message, no body). Step 7's cleanup is its own commit so the diff between "moved" and "removed old home" is reviewable.

If any step turns out to be bigger than expected (e.g. test rewrites spill into multiple files), split at file boundaries within the step — the rule is "one logical change per commit."

## Verification summary

After all steps:

- No file in the codebase imports `InstallSession` or `BackgroundInstallSession`.
- `_Canceled` has one home: `genesis_worker.utils.background_session`.
- `BackgroundSession` has three concrete subclasses outside the contract: `HfAcquireSession`, `GithubReleaseAcquireSession`, `DockerPullAcquireSession`, `UvToolAcquireSession`.
- All three install flavors reuse the same runtime (thread, cancel, log, terminal) — no per-flavor duplication.
- Adding a new install flavor (apt, snap, conda) means one new file in `utils/acquire/` that subclasses `BackgroundSession`.
