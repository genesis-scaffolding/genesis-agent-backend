# Plan 005: Service install

Implements [spec-005](../specs/spec-005-service-install.md).

## Working rules

- Branch: `feature/service-install` from `main`.
- No new dependencies (urllib is stdlib; yaml is already a dep per ADR-006).
- The running llama-swap on `:8080` is **not** stopped. Validation uses a scratch install root.
- `bin/`, `Makefile`, `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json` are **not** modified (ADR-008).
- Commit per logical chunk. Wait for user verification before commit (AGENTS.md).
- Merge: `git merge --no-ff feature/service-install` on `main` after the user signs off.
- Validation gates per ADR: `uv run pytest -q`, `uv run pyright`, `uv run ruff check genesis_worker`.

## Chunk 1 — ABC, layout, manifest

1. **`genesis_worker/contracts/install.py`** — `InstallState` (StrEnum), `InstallVersion` (NamedTuple), `ServiceInstall` (ABC), `InstallSession` (ABC). Document `kind` extensions on `AcquireStep` in a comment block at the top.
2. **`genesis_worker/contracts/service.py`** — `can_install: bool = False` on `ServiceCapabilities`. `installs() -> list[ServiceInstall]` returning `[]` on `InferenceService`.
3. **`genesis_worker/contracts/__init__.py`** — re-export the new types.
4. **`genesis_worker/utils/install/__init__.py`** — empty package.
5. **`genesis_worker/utils/install/manifest.py`** — `Manifest` frozen dataclass with `from_yaml(path)` and `to_yaml(path)` helpers. YAML via `yaml.safe_load` / `yaml.safe_dump` (per ADR-006). `verified: bool = False`, optional fields default to `None`.
6. **`genesis_worker/utils/install/layout.py`** — `InstallLayout` class with constructor `(data_dir, state_dir, name)`. Methods: `installs_root() -> Path`, `manifest_path(version) -> Path`, `binary_path_for(version, binary_rel: str)`, `current_symlink() -> Path`, `resolved_selection() -> str | None` (reads `selections.yaml`; falls back to the `current` symlink target's version directory name; `None` if neither exists), `set_selection(version: str)` (writes `selections.yaml` atomically via `os.replace()` on a sibling temp — even though v1's UI is read-only, the helper is in place).
7. **`tests/test_manifest.py`** — round-trip a sample MANIFEST; assert optional fields default to `None` / `False`.
8. **`tests/test_install_layout.py`**:
   - empty data dir → `resolved_selection()` is `None`
   - one install + `current` symlink → resolves to that version
   - two installs + `current` → only `current`'s target wins
   - `selections.yaml` pinning → that version wins when present
   - pinned-to-uninstalled → falls back to `current`, else `None`
   - `set_selection()` writes the YAML and the next read picks it up
9. **`uv run pytest`, `uv run ruff check genesis_worker`, `uv run pyright`** — all clean.
10. Commit chunk 1.

## Chunk 2 — GitHub release tarball backend

11. **`genesis_worker/services/llama_swap/installs.py`** — the backend, the streaming session, and the two installables all live in this single file (chunk 3 adds the installables; chunk 2 lands the backend + session first).

    Module-top helpers (private):
    - `_http_get_json(url, *, timeout=30)` — `urllib.request.Request` with `User-Agent: genesis-worker`, `Accept: application/json`.
    - `_http_download(url, dest, *, progress, cancel, timeout=600)` — streams the body 64 KB at a time, calling `progress(done, total)` between chunks; `_Canceled` is raised (and `dest` unlinked) when `cancel()` returns True.
    - `_sha256(path)` — streamed SHA-256.
    - `_extract(archive, dest)` — tarfile, falling back to zipfile.
    - `_parse_checksums(text, target)` — picks the line `<hex> <filename>` whose filename matches.

    `class GithubReleaseTarball` (constructor-only; the session is the worker):
    - `(name, repo_owner, repo_name, layout, cache_root, asset_for, binary_rel, checksums_url=None, install_method="github_release_tarball")`.
    - `available_versions() -> list[InstallVersion]` — reads `${GENESIS_INSTALL_GITHUB_API:-https://api.github.com}/repos/<owner>/<repo>/releases/latest`, picks the asset via `asset_for(assets)`, optionally fetches `checksums_url(release)` for sha256.
    - `install(*, version=None)` returns an `InstallSession`.

    `class _GithubReleaseInstallSession(InstallSession)` runs work in a daemon thread:
    - `_state.step` is the live `AcquireStep`; `current_step()` returns it.
    - Thread walks `fetching (releases JSON) → fetching (bytes via _progress callback) → verifying (sha256) → extracting (tarfile) → complete`. Cancel → `kind="cancelled"`. Exception → `kind="failed"` with `error=...`.
    - On success: writes `Manifest`, calls `layout.set_current_symlink(version)` (atomic).
    - On failure/cancel: partial cache files and partial install dirs are unlinked before the terminal step is emitted.
12. **`tests/test_install_tarball.py`** — against a local `http.server` on a free port, mirroring the pattern in `tests/test_lifecycle.py`:
    - GET `/repos/o/r/releases/latest` returns a canned release JSON with two assets; the asset callback picks one and the tarball lives beside the JSON response.
    - Drive `install(version="v0.4.5")`, poll `current_step()` until `complete`. Assert the binary lands at the expected path, MANIFEST is written, `current` symlink points at the version.
    - SHA256-mismatch variant: a separate test where the asset's apparent sha256 (from a `checksums.txt` route) does not match. Assert `step.kind == "failed"`, `current` still points at the prior version, no `<version>/` left behind.
    - Cancel mid-fetch: a separate test that pauses the download (large fake body) and calls `cancel()` mid-`fetching`. Assert `step.kind == "cancelled"`, partial file removed.
13. **`uv run pytest`, etc.** — all clean.
14. Commit chunk 2.

## Chunk 3 — Llama-swap installables and lifecycle hookup

15. **`genesis_worker/services/llama_swap/installs.py`**:
    - `LlamaSwapBinary(ServiceInstall)` — `name = "llama-swap"`. `repo_owner = "mostlygeek"`, `repo_name = "llama-swap"`, asset matcher picks `{asset_name}_Linux_{arch}.tar.gz`, `binary_rel = ""` (binary sits at the archive root for GoReleaser tarballs).
    - `LlamaServerBinary(ServiceInstall)` — `name = "llama-server"`. `repo_owner = "ai-dock"`, `repo_name = "llama.cpp-cuda"`, asset matcher picks `llama.cpp-cuda-*.tar.gz`, `binary_rel = "bin/llama-server"` (binary lives under the upstream tree).
    - Both accept `(data_dir, cache_dir, state_dir)` in their constructor, derive an `InstallLayout`, and compose a `GithubReleaseTarball` for `available_versions()` and `install()`.
16. **`genesis_worker/services/llama_swap/service.py`**:
    - Construct `LlamaSwapBinary` and `LlamaServerBinary` in `__init__` from `ctx.data_dir`, `ctx.cache_dir`, `ctx.state_dir`.
    - `installs()` returns `[self._llama_swap_install, self._llama_server_install]`.
    - `capabilities()` adds `can_install=True`.
    - `is_available()` is `self._llama_swap_install.binary_path() is not None`. Remove the `shutil.which` call.
    - `start()` resolves `binary = self._llama_swap_install.binary_path()`. If `None`, return `StartResult(ok=False, message="llama-swap binary not installed")`. Else pass to `lifecycle.start_swap`.
17. **`genesis_worker/services/llama_swap/lifecycle.py`**:
    - Add `import shlex`.
    - Change `start_swap` first parameter from `config` to `binary: Path`. Validate `binary.is_file()` before any tmux activity.
    - `shlex.quote` every interpolation into the tmux command string.
    - Update internal usage to drop the implicit `which("llama-swap")` precheck (the caller already validated).
18. **`tests/test_lifecycle.py`**:
    - Drop the PATH monkeypatch from `fake_swap_env`. Pass `binary=shim_path` explicitly.
    - Add `test_start_fails_when_binary_missing` — missing `binary` returns `ok=False` with the new message and does **not** touch tmux.
    - Existing tests still pass.
19. **`tests/test_service_llama_swap.py`**:
    - Assert `service.installs()` returns two entries with the right `name`s.
    - With no installs laid down, `service.is_available()` is False and `service.start()` returns `ok=False`.
    - With a fake install laid down at the expected path (a 5-line shell script that `exit 0`s), `service.is_available()` is True.
20. **`uv run pytest`, etc.** — all clean.
21. Manual smoke: `uv run python -c "..."` instantiates the service in a scratch `data_dir`, runs `LlamaSwapBinary.install()` against a fake GitHub server, and checks the binary lands. Code mirrors the test case — repeats it once interactively to confirm it works outside pytest.
22. Commit chunk 3.

## Chunk 4 — UI

23. **`genesis_worker/services/llama_swap/ui/binaries.py`** — Binaries page.
    - Iterate `worker.service("llama_swap").installs()`. One `st.expander` per installable.
    - For each expander: header with state badge and resolved selection. Body lists `available_versions()` (cached per session-id to avoid API thrash), with the current selection highlighted. Buttons: `Install`, `Reinstall`, `Uninstall`, `Cancel`.
    - When an install is in flight, render an `@st.fragment(run_every="2s")` that reads `session.current_step()` and renders either a `st.progress(...)` from `AcquireProgress` or a terminal state badge.
    - Sessions are stored in `st.session_state["install_sessions"][install_name]` keyed on the latest submitted action.
    - Cancel button just calls `session.cancel()`.
24. **`genesis_worker/services/llama_swap/service.py`** — `ui_pages` property adds `UiPage("Binaries", ":material/inventory_2:", ui_dir / "binaries.py")` between Status and Config editor.
25. **`genesis_worker/services/llama_swap/ui/status.py`** — add a compact Binaries section after Service info:
    - One row per installable: badge + resolved version + status text + "Manage binaries →" button that calls `st.switch_page` against the Binaries page (same `to_relative` pattern).
26. Smoke: `uv run streamlit run ...` against the dashboard. Confirm the Status page shows the Binaries section, the Binaries page renders, install/reinstall/cancel flows complete. Logs to `/tmp/streamlit.log`.
27. Commit chunk 4.

## Chunk 5 — Validation

28. `uv run pytest -q` — all pass.
29. `uv run pyright` — exit 0.
30. `uv run ruff check genesis_worker` — exit 0.
31. `tests/test_plugin_boundary.py` — manually extend if needed (probably nothing needed since `installs.py` and `ui/binaries.py` mirror the existing rules).
32. Confirm running llama-swap on `:8080` is still serving (`curl -s http://127.0.0.1:8080/v1/models`).
33. `git status` — confirm `bin/`, `Makefile`, `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.*`, `pi-models.json` are untouched.
34. Wait for user verification before final commit + merge.

## Notes

- The fake GitHub server in tests mirrors the pattern from `tests/test_lifecycle.py`. Bind a free port, serve canned responses, drive `install()` against it.
- The shared fragment component for rendering install/acquire progress goes in `genesis_worker/utils/ui/` only if a third caller appears in this plan; v1 has the Status-page console and the new Binaries-page progress, both of which can be inline.
- Pin-write (`selections.yaml` from UI) is **not** wired in v1. The helper exists for chunks 1+3 but no button exposes it; v1+1 adds a small UI control.
- `LlamaServerBinary`'s artifact is staged but unwired. The recipe still names `vendor/llama.cpp/build/bin/llama-server`. Integration lands when recipes migrate (ADR-008 phase 10).
- uv-tool install is **not** implemented in this plan — both v1 installables use the GitHub release backend. uv-tool lands in a future plan when a Python-based service (e.g. ComfyUI) requires it.
