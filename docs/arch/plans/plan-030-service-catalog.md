# Plan: Service categorization + enable/disable (Service Catalog)

Steps for ADR-029. Each numbered section is an independently committable boundary. End-of-section verification keeps partial state green at every commit point.

## Step 1 — `ServiceCategory` enum + `category` / `description` on the ABC

**Files:**
- `genesis_worker/contracts/service.py`:
  - Add `ServiceCategory(StrEnum)` with values `LLM | IMAGE | CHAT | CRAWLER | MEDIA | UTILITY | OTHER`. Frozen string values; iteration order is the dashboard display order.
  - Add `InferenceService.category: ServiceCategory` property returning `ServiceCategory.OTHER` (default).
  - Add `InferenceService.description: str` property returning `""` (default).
  - Export `ServiceCategory` from `contracts/__init__.py`.

No behavior change yet — every existing service lands in `OTHER`. The dashboard and registry ignore these fields.

**Verification:** `uv run pytest -q` passes unchanged.

## Step 2 — State file IO helper

**File:** `genesis_worker/utils/state/enabled_services.py` (new package: `genesis_worker/utils/state/__init__.py`)

Pure functions, no class:

```python
def load_enabled_set(state_dir: Path) -> set[str] | None
def save_enabled_set(state_dir: Path, names: set[str]) -> None
```

- YAML format (`enabled: list[str]`), atomic write via tmp + `os.replace`. Mode `0o600`.
- `load` returns `None` when the file doesn't exist (caller distinguishes first-run).
- No validation against any service list — stale names are silently ignored when the registry resolves them.

**Tests:** `tests/test_enabled_services_state.py` — round-trip, missing-file returns None, atomic write doesn't leave tmp behind, sort-stable ordering.

## Step 3 — `ServiceRegistry` enable/disable + bootstrap

**File:** `genesis_worker/registries.py`

`ServiceRegistry.__init__` slots in the enabled-set bootstrap after the loop that constructs instances:

```python
self._enabled: set[str] = self._load_or_bootstrap()
```

`_load_or_bootstrap`:

- Try `load_enabled_set(state_dir)`. If non-None, return it.
- Otherwise: iterate `self._instances.values()`, call `svc.is_available()`, collect names where True. `save_enabled_set` and return.

New methods:

```python
def is_enabled(self, name: str) -> bool
def enabled_names(self) -> set[str]
def enable(self, name: str) -> None      # idempotent; KeyError on unknown
def disable(self, name: str) -> None     # RuntimeError when svc.is_running()
def enabled(self) -> list[InferenceService]   # filtered, order = registration order
def disabled(self) -> list[InferenceService]  # filtered
```

`disable` calls `self._instances[name].is_running()` first; raises `RuntimeError("cannot disable X: service is running — stop it first")` if so.

`enable` / `disable` call `_persist()` (which calls `save_enabled_set`).

**Tests:** `tests/test_services_registry_enable_disable.py`
- Bootstrap writes the file when missing and includes only `is_available()` services.
- Bootstrap is skipped when the file exists.
- `enable` is idempotent; persists.
- `disable` raises when `is_running` is True.
- `disable` is idempotent when already disabled.
- Unknown service raises `KeyError` on `enable`; `disable` raises too (lookup before checking running).
- `enabled()` / `disabled()` partition correctly.

## Step 4 — `ServiceInfo` extension + facade method

**Files:**
- `genesis_worker/utils/models.py`: add `category: ServiceCategory` and `description: str` fields to `ServiceInfo`. Default factory values let existing call sites stay valid.
- `genesis_worker/facade.py`:
  - Update `list_services()` to populate `category=svc.category, description=svc.description`.
  - Add `list_enabled_services() -> list[ServiceInfo]` that filters by `self._service_registry.enabled_names()`.

**Verification:** `uv run pyright` flags any test that constructs `ServiceInfo` positionally — fix the affected tests in this step.

**Tests:** Add coverage in `tests/test_facade.py` for `list_enabled_services()` filtering.

## Step 5 — Per-service `category` declarations

**Files** (5):

- `genesis_worker/services/llama_swap/service.py`: `category = ServiceCategory.LLM`.
- `genesis_worker/services/comfyui/service.py`: `IMAGE`.
- `genesis_worker/services/sillytavern/service.py`: `CHAT`.
- `genesis_worker/services/cptr/service.py`: `CHAT`.
- `genesis_worker/services/crawl4ai/service.py`: `CRAWLER`.

Each service also declares a `description: str` on the same property pair, **kept to one short sentence (~25–30 chars)** so the Service Catalog rows don't vary in height. The copy:

| Service | description |
|---|---|
| llama_swap | `"OpenAI-compatible LLM server"` |
| comfyui | `"Node-based image generation"` |
| sillytavern | `"LLM chat front-end"` |
| cptr | `"Open WebUI automation"` |
| crawl4ai | `"Web crawler + dashboard"` |

If a future service needs more than a short caption, it belongs on its own landing page — not in the catalog row.

These are additive — default `OTHER` would compile, but the rule for new services is to declare one explicitly. Document this in AGENTS.md at the end.

**Verification:** `uv run pyright genesis_worker/services` clean.

## Step 6 — Rename `Catalog` → `Model Catalog`

**Files:**

- `genesis_worker/ui/app.py`: sidebar label `"Catalog"` → `"Model Catalog"`.
- `genesis_worker/ui/catalog.py`: page title already reads `"Model Catalog"`; no change needed.
- `genesis_worker/tests/test_app_shell.py`: update `_FRAMEWORK_UI / "catalog.py"` assertions (file path stays; only label changes — no test change required for the path itself, but a label assertion would be added if present).

**Verification:** `uv run pytest -q` passes; manual smoke: sidebar shows "Model Catalog".

## Step 7 — New Service Catalog page

**Files:**

- `genesis_worker/ui/services_catalog.py` (new). Imports nothing from plugins; reads only via the facade.
- `genesis_worker/ui/app.py`: register the new page in the "Overview" group with sidebar label `"Service Catalog"`.
- `genesis_worker/tests/test_app_shell.py`: extend path assertion to include the new file.
- `genesis_worker/tests/test_services_catalog_ui.py` (new): page parses with `ast.parse`; renders without errors against a `MagicMock(spec=InferenceService)` list.

Layout of the new page:

```
# Service Catalog
  (intro caption: "Enable / disable the services that appear on the dashboard and sidebar.")

  for category in ServiceCategory:
    services_in_cat = [s for s in all_services if s.category == category]
    if not services_in_cat: continue
    with st.container(border=True):
      st.subheader(category.label)
      for svc_info in services_in_cat:
        svc = worker.service(svc_info.name)
        status = worker.service_status(svc_info.name)
        with st.container(border=True):
          cols = st.columns([4, 2, 1])
          cols[0].markdown(f"**{svc_info.display_name}**")
          cols[0].caption(svc_info.description or "(no description)")
          cols[1].badge(status.state.value, color=...)
          toggle_disabled = svc.is_running()
          new_value = cols[2].toggle(
              "Enabled",
              value=worker.services.is_enabled(svc_info.name),
              disabled=toggle_disabled,
              key=f"enable-{svc_info.name}",
              label_visibility="collapsed",
          )
          if new_value != worker.services.is_enabled(svc_info.name):
              if new_value:
                  worker.services.enable(svc_info.name)
              else:
                  worker.services.disable(svc_info.name)
              st.rerun()
```

Where `category.label` is a `dict` mapping `ServiceCategory -> str` defined in `services_catalog.py` (local to the page — not a contract concern).

**Verification:** `uv run pytest -q` passes. Manual smoke: page lists every service; toggling one updates the dashboard on the next page load.

## Step 8 — Dashboard grouping + enabled-only filter

**File:** `genesis_worker/ui/dashboard.py`

Replace the existing "Services" container with a grouped layout:

```python
with st.container(border=True):
    st.header("Services")
    services_by_cat: dict[ServiceCategory, list[ServiceInfo]] = {}
    for info in worker.list_enabled_services():
        services_by_cat.setdefault(info.category, []).append(info)
    if not services_by_cat:
        st.info("No services enabled. Visit Service Catalog to enable some.")
        return  # exit the container early
    for category in ServiceCategory:
        infos = services_by_cat.get(category, [])
        if not infos:
            continue
        st.subheader(category.label)
        # existing 3-column grid for this batch
        ...
```

Add a small caption below the "Services" header: `"Disabled services are hidden. Enable them in Service Catalog."` (always visible — helps discovery).

**Verification:** dashboard renders correctly when all categories have enabled services and when some categories are empty.

## Step 9 — Sidebar enabled-only filter

**File:** `genesis_worker/ui/app.py`

```python
for svc_info in worker.list_enabled_services():
    svc = worker.service(svc_info.name)
    nav[svc_info.display_name] = [
        _page(p.path, p.label, p.icon, p.url_path) for p in svc.ui_pages
    ]
```

The "Overview" group (Dashboard / Model Catalog / Service Catalog) remains always present.

**Verification:** sidebar only shows enabled services.

## Step 10 — Docs

- `genesis-agent-backend/AGENTS.md`: under plugin authoring, add the rule: *"Every new `InferenceService` subclass must override `category` to declare its group. Use `OTHER` only as a placeholder; the first thing a new service should do after construction is pick a real category."*
- New `docs/arch/adr-029-service-catalog.md` (Nygard format, links to this plan).

## Step 11 — Full gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

All must pass. Live smoke: enable/disable from the new page; confirm the dashboard reflects the change after navigation; confirm the bootstrap on a fresh tmp state_dir auto-enables a mocked `is_available=true` service.

## Test additions summary

- `tests/test_enabled_services_state.py` — load/save round-trip + atomic write.
- `tests/test_services_registry_enable_disable.py` — bootstrap + enable/disable + running guard.
- `tests/test_services_catalog_ui.py` — page parses + renders.
- `tests/test_facade.py` — `list_enabled_services()` filtering (new test, not modifying existing).
- Per-service tests: no change needed (default `category=OTHER` keeps them green).

## Migration / backwards-compat

- Existing users with no state file: bootstrap auto-enables installed services → identical visible behavior.
- New services added after bootstrap: start disabled. User enables them on the Service Catalog page.
- `ServiceInfo` gains two fields with defaults — only direct-construction call sites need updating.
- `category` defaults to `OTHER` — existing services land in "Other" on the dashboard until step 5 declares them.

## Out of scope

- Settings-driven enable list (`GENESIS_ENABLED_SERVICES=…`). State file only in v1.
- Toggling enable/disable directly from the dashboard. Catalog page only.
- Cross-tab consistency if the same dashboard is opened in two browser windows.
- Bulk operations on the Service Catalog page (enable all / disable all). v1 is per-row.
