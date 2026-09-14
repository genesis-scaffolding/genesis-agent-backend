# Plan: ADR-034 — runtime user-overrides + in-place facade refresh

Implements ADR-034. The work splits into four layers, each one
independently testable.

## File-by-file

### 1. New utility — `genesis_worker/utils/config_overrides.py`

Leaf utility holding the dotenv read/write helpers. Allowed imports:
stdlib + third-party only (boundary test enforces).

- `read_user_overrides(path: Path) -> dict[str, str]`
  - Missing file → empty dict.
  - Comment lines (`#`) and blank lines → skipped.
  - Malformed line → raises with line number.
- `write_user_overrides(path: Path, values: dict[str, str]) -> None`
  - Atomic write: `tmp` sibling + `os.replace`.
  - Mode `0o600`.
  - Sorted keys for stable diffs.

We deliberately do **not** introduce a new dependency for the dotenv
parser — the format is small enough to hand-roll, and adding
`python-dotenv` to a leaf utility broadens the dependency surface for
five lines of code. pydantic-settings already pulls `python-dotenv`
in transitively, but that's a runtime dep we don't need to lean on
here. The hand-rolled reader matches what we need (no quoting edge
cases, no variable interpolation).

### 2. Settings layering — `genesis_worker/settings.py`

- Add a module-level helper `_read_user_overrides_file(config_dir) -> dict[str, str]`
  that resolves `<config_dir>/user-overrides.env` and delegates to
  `utils.config_overrides.read_user_overrides`.
- Replace `_default_settings()` to layer the override file. Two
  paths:
  - Read the override file's values into a dict.
  - Construct `Settings(_env_file=None, **override_values)`.
  pydantic-settings' `_env_file=None` suppresses the `dev.env` /
  `.env` files; the real env still wins (because real env > override
  file per ADR precedence). Constructor args still win over both.

  Wait — that's wrong. Constructor args are *above* the override
  file in our chain. The right approach is:

  - Construct `Settings(_env_file=None)` (no file reads; reads from
    real env only).
  - Then call `.model_copy(update={"...": override_value})` for each
    key in the override file.

  Actually pydantic's `model_copy(update=...)` is shallow and
  pydantic-settings has special handling for nested fields. The
  cleanest path: construct `Settings()` with the override file added
  to the `env_file` tuple.

  Let me reconsider: pydantic-settings' `env_file=` accepts a tuple
  of file paths. Files are processed in order with later files
  winning. So `env_file=("dev.env", ".env", "<config_dir>/user-overrides.env")`
  gives exactly the precedence we want, **as long as** the override
  file exists when `Settings()` is called.

  On first run (no override file), we want no error. Two options:
  - Pass `env_file=("dev.env", ".env")` when the override file is
    absent, and the full tuple when it's present. This requires
    `Settings()` to know the config_dir before reading it.
  - Touch the file on first run with empty content, so it always
    exists.

  We'll go with the first option: resolve `config_dir` lazily inside
  `_default_settings()` (or a new `_build_settings()` helper) and
  pass the appropriate tuple.

- The default `config_dir` factory already calls `xdg_path("CONFIG", ".config", XDG_BASE)`,
  so the file path is well-defined. `PathsSettings` needs no
  changes.

### 3. Facade additions — `genesis_worker/facade.py`

Add five methods on `GenesisWorker`:

- `user_overrides_path() -> Path` — returns `<config_dir>/user-overrides.env`.
- `read_user_overrides() -> dict[str, str]` — wrapper over the
  utility.
- `write_user_overrides(values: dict[str, str]) -> None` — wrapper
  over the utility. Validates that `values` doesn't try to set
  `GENESIS_SECRETS__*` (those belong to the secrets access path;
  silently stripping them silently). Raises `ValueError` if so.
- `refresh_config() -> None` — the in-place rebuild. Construct first,
  swap last.
- `snapshot_settings() -> list[SettingSnapshot]` — returns
  `[(name, resolved_value, source_label), ...]` for the framework
  knobs the Settings page renders.

A new dataclass `SettingSnapshot` lives alongside `ServiceInfo` /
`SourceInfo` in `genesis_worker/utils/models.py` (the existing
view-types module) and is framework-only — never crosses the
plugin boundary.

`_build_settings()` becomes a free function (or a module-level
helper) so both `_default_settings()` and `refresh_config()` use it.
It's a thin wrapper that resolves `config_dir`, layers the override
file, and returns a `Settings`.

### 4. Streamlit page — `genesis_worker/ui/settings.py`

Framework page registered in `ui/app.py`'s Overview section.

Sections:

1. **Effective settings** — read-only. Renders `snapshot_settings()`
   as a table with three columns: name, resolved value, source
   badge. Knobs listed: `vault_path`, `data_dir`, `config_dir`,
   `cache_dir`, `state_dir`, `log_dir`, `huggingface.local_path`,
   `lmstudio.local_path`.
2. **User overrides** — form. Each known knob has an "Override"
   checkbox and a text input. The "extra overrides" textarea at the
   bottom is parsed by `utils.config_overrides.read_user_overrides`
   (or a string-only variant) and merged with the form fields.
   **Save overrides** writes the merged dict and calls
   `refresh_config()` inside a try/except.
3. **Status banner** — after save, lists running services with a
   link to each one's Status page (using `svc.ui_pages[0]`). For
   framework-only edits this banner is empty (no service options
   changed).

`ui/app.py` adds `_page(_FRAMEWORK_UI / "settings.py", "Settings", ":material/settings:", None)`
to the Overview nav, after Service Catalog.

### 5. Tests

- `genesis_worker/tests/test_user_overrides_io.py` — round-trip,
  atomic write, mode 0o600, comment preservation isn't a goal
  (the page rewrites the whole file; comments are preserved as a
  side effect only when re-parsing values that weren't changed).
- `genesis_worker/tests/test_settings.py` additions — three tests:
  user-overrides.env layered above `.env`; missing file is a
  no-op; malformed file surfaces a clear error.
- `genesis_worker/tests/test_facade.py` additions — five tests:
  - `user_overrides_path()` returns the expected path.
  - `read_user_overrides()` / `write_user_overrides()` round-trip.
  - `refresh_config()` rebuilds registries but preserves the
    facade object identity (`worker is worker_after_refresh`).
  - `refresh_config()` drops the catalog cache.
  - `refresh_config()` raises on a malformed override and leaves
    the existing facade state intact (the "construct-first,
    swap-last" guarantee).
- `genesis_worker/tests/test_settings_ui.py` — render the page via
  Streamlit's `AppTest`, simulate a save, assert the file landed
  and `refresh_config()` was called.

### 6. Optional: refresh the dashboard's debug panel

The existing debug panel in `genesis_worker/ui/dashboard.py` already
shows the resolved vault path and a hand-rolled env walk. We can
replace the env walk with `worker.snapshot_settings()` so both
surfaces share one implementation. This is a small drive-by —
defer if it grows the diff.

### 7. What we are NOT changing

- The contract surface (`genesis_worker.contracts`) — unchanged.
- Plugin code under `sources/` and `services/` — unchanged.
- The FastAPI surface — read-only methods are reachable via
  `worker.*` but we don't add new routes.
- The orchestrator's Ansible playbooks — out of scope.

## Order of execution

1. `utils/config_overrides.py` + its test. Pure utility, no deps.
2. `settings.py` layering + `test_settings.py` additions.
3. `facade.py` additions + `test_facade.py` additions.
4. `ui/settings.py` + `test_settings_ui.py`.
5. `ui/app.py` nav update.
6. Run the gate: `make test`.

## Validation

The four commands from `AGENTS.md`:

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

All must pass before declaring done.

## Commit & merge

- One branch: `feature/runtime-user-overrides`.
- One commit per logical layer (utility, settings, facade, UI,
  tests) keeps the history readable. Each commit must leave the
  tree green (`make test`).
- Do not commit until user verifies and approves.
- Merge: `git merge --no-ff feature/runtime-user-overrides`, then
  `git push`.