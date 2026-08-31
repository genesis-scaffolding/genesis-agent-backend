# ADR-029: Service catalog — categorization + enable/disable

## Title
Service catalog — categorization on the `InferenceService` ABC, plus first-class enable/disable state with a "Service Catalog" page.

## Status
Proposed.

## Context

The fleet is growing. Today the worker auto-discovers five `InferenceService` plugins (llama-swap, comfyui, sillytavern, cptr, crawl4ai) and renders them in a single undifferentiated grid on the dashboard and one section per service in the sidebar. New services on the roadmap — image generators (A1111, Fooocus), chat UIs (Open WebUI), media servers (Jellyfin, Navidrome, Audiobookshelf), crawlers, utilities — push that count past the point where the flat display is usable. Two problems follow.

**1. The user can't tell services apart at a glance.** Today's only metadata surfaced is `display_name` and `capabilities`. Two "Chat UIs" or two "Media servers" collapse into identical-looking cards. The dashboard has no way to group related services (LLM serving under "LLM Inference"; media servers under "Media"; etc.) because the framework doesn't know the group's name.

**2. Out-of-the-box the worker ships with every discovered service enabled.** A user adding a service they don't intend to run still sees its dashboard tile, its sidebar entry, and its status refresh tick. As the service count grows, the dashboard becomes a wall of irrelevant cards and the sidebar becomes a wall of irrelevant sections. The user has no way to opt out.

These two concerns share a solution surface: both need new metadata on the `InferenceService` ABC, both need new state at the framework level, both need new UI to expose it. They are bundled here because adding them in two separate ADRs would force the dashboard to be touched twice and the framework contract to drift through two intermediate states.

The "Service Catalog" page concept addresses the discoverability problem: a meta-view of every service the worker knows about, regardless of whether it's enabled, where the user can flip the new enable/disable toggle. Disabled services vanish from the dashboard and sidebar; the catalog page remains visible because it lives in the framework's "Overview" group.

## Decision

### 1. New contract surface on `InferenceService`

```python
class ServiceCategory(StrEnum):
    LLM = "llm"
    IMAGE = "image"
    CHAT = "chat"
    CRAWLER = "crawler"
    MEDIA = "media"
    UTILITY = "utility"
    OTHER = "other"

class InferenceService(Plugin):
    @property
    def category(self) -> ServiceCategory:
        return ServiceCategory.OTHER

    @property
    def description(self) -> str:
        return ""
```

Both are properties with defaults. Existing services and any future plugin that forgets to override them land in `OTHER` and render with no description — not a failure mode, but a visible nudge to update. `ServiceCategory` is iteration-ordered; the dashboard iterates the enum and skips empty categories, so adding a new value later is a non-breaking change.

`ServiceInfo` (in `genesis_worker/utils/models.py`) gains `category` and `description` fields so UI pages read everything from the view type without re-dispatching through `worker.service(name)`.

### 2. Enable/disable state storage

State file at `<state_dir>/enabled_services.yaml`:

```yaml
enabled:
  - llama_swap
  - crawl4ai
  - jellyfin
```

YAML is consistent with the rest of the project's state files (ADR-006). The file lives under `state_dir` because the enable set is user-mutable state, not configuration (ADR-004 XDG layout). Atomic writes via tmp + `os.replace`, mode `0o600`.

A new module `genesis_worker/utils/state/enabled_services.py` owns the read/write:

```python
def load_enabled_set(state_dir: Path) -> set[str] | None: ...
def save_enabled_set(state_dir: Path, names: set[str]) -> None: ...
```

`load` returns `None` when the file is absent — the registry treats that as "first run" and triggers bootstrap (next section). Stale names in the file (services that no longer exist) are silently ignored when the registry resolves them; the file is rewritten on the next mutation.

### 3. First-run bootstrap

When `ServiceRegistry.__init__` finds no state file, it iterates the constructed instances and calls `svc.is_available()`. Services that report `True` (binary installed, image pulled, etc.) are added to the enabled set; the file is written; subsequent boots read it instead of re-probing. This matches the user's stated intent: *"out of the gate, services would be disabled, until user enable them, or they are already installed and ready to run"* — the bootstrap is the "or they are already installed" branch, executed exactly once.

Bootstrap is intentionally one-shot: it does not re-run on later installs, upgrades, or settings changes. A user who installs a new image later sees the new service start disabled and must enable it on the Service Catalog page. This is the predictable behavior — auto-enable-after-the-fact is what we are explicitly avoiding.

For services whose `is_available()` is non-trivial (docker images, network probes), the bootstrap is the cost of first launch only. Subsequent launches skip it because the file exists.

### 4. Registry API

```python
class ServiceRegistry(_Registry):
    def is_enabled(self, name: str) -> bool
    def enabled_names(self) -> set[str]
    def enable(self, name: str) -> None       # idempotent; KeyError on unknown
    def disable(self, name: str) -> None      # RuntimeError when is_running()
    def enabled(self) -> list[InferenceService]
    def disabled(self) -> list[InferenceService]
```

`enable` and `disable` persist immediately. `disable` checks `svc.is_running()` first and refuses with `RuntimeError("cannot disable X: service is running — stop it first")`. This implements *"only services that are not currently on can be turned off"*. The check is server-side authoritative — the UI also disables the toggle widget while running, but the framework is the source of truth.

`GenesisWorker` gains:

```python
def list_enabled_services(self) -> list[ServiceInfo]:
    """Return display info for enabled services only. Used by dashboard and sidebar."""
```

The existing `list_services()` continues to return every service — the catalog page and any future CLI listings need the unfiltered set.

### 5. UI changes

**Sidebar (`ui/app.py`):** the loop that builds per-service nav sections iterates `worker.list_enabled_services()` instead of `worker.list_services()`. The framework-owned "Overview" group (Dashboard / Model Catalog / Service Catalog) remains always-present so users can always reach the catalog page.

**Dashboard (`ui/dashboard.py`):** the "Services" container groups by `ServiceCategory`. Layout is one bordered container with a subheader per non-empty category and the existing 3-column card grid inside each subheader. Categories are emitted in `ServiceCategory` iteration order, so the visual order is stable. Empty categories produce no subheaders — no empty containers.

**Service Catalog page (new, `ui/services_catalog.py`):** lists every service (enabled + disabled) grouped by category. Each row carries display name, description, status badge, and an `st.toggle` for enable/disable. The toggle is `disabled=True` when `svc.is_running()`, mirroring the framework guard. Toggling mutates the registry and calls `st.rerun()` so the next render reflects the change.

The existing model catalog page (`ui/catalog.py`) is renamed in the sidebar from "Catalog" to "Model Catalog" so the two catalog pages are unambiguous.

### 6. Plugin authoring rule

`AGENTS.md` adds a rule: every new `InferenceService` subclass must override `category`. `OTHER` is a stopgap, not a destination. This is documentation, not enforcement — a future test could reject `category == OTHER` for any plugin, but doing so now would break the bootstrap default for hypothetical plugins that legitimately are "Other" (e.g. an internal utility we haven't bucketed yet).

## Consequences

**Positive:**

- The dashboard groups related services visually, making the growing fleet navigable.
- The sidebar stays compact as the service count grows — disabled services are simply absent.
- The Service Catalog page gives users a coherent place to manage the set, rather than scattering enable/disable across per-service admin pages.
- New services that ship with the worker don't auto-appear on the dashboard; users opt in. This is the right default for a system that grows over time.
- Migration is silent: existing users with no state file get the same set of enabled services as before (because installed services auto-bootstrap), so the v1 → v2 transition is invisible unless the user explicitly disables something.
- `ServiceCategory` is a small fixed enum; adding a value later is a non-breaking change for existing services (their `category` property remains valid, they just don't use the new value).
- The framework remains the authority on enable/disable: UI can't disable a running service because the registry refuses, even if a buggy client posts the wrong state.

**Negative:**

- `ServiceInfo` grows by two fields. Direct construction sites need updating — covered by step 4 of the plan.
- Bootstrap cost: first launch walks every service's `is_available()`. For docker-backed services that's one daemon call per service. Acceptable as a one-time cost; not a recurring expense.
- The Service Catalog page duplicates a small amount of metadata (display name, description) that already appears on per-service landing pages. Not a real cost; the duplication is the affordance.
- A user who wants config-as-code (provision the worker with a known enabled set on a fresh machine) cannot do so via env vars in v1 — they have to write the YAML file as part of provisioning. Settings-driven seeding is deferred.
- The category enum is a closed set. "Non-AI media" + "AI image" are different categories today, but a future "video generation" service might land ambiguously between `IMAGE` and `MEDIA`. If that ambiguity becomes a recurring problem, the right fix is a hierarchy (category + subcategory) or a tag list — both deferred.
- Documentation rule (`category` must be set) is not enforced; a developer adding a new service might forget. The dashboard putting `OTHER` services in a less prominent group is the visible nudge, not a code-level guard.

**Neutral:**

- The `description` property defaults to `""`; UI pages render `(no description)` for unset entries. No migration cost.
- "Catalog" → "Model Catalog" sidebar rename is purely cosmetic but worth doing in this same change so the two catalog pages are unambiguous from the start.
- The Service Catalog page lives under "Overview" so it's always reachable, even when every service is disabled. This is the correct placement — it's a meta-view, not a service-specific page.

## Plan

`docs/arch/plans/plan-030-service-catalog.md` — eleven-step execution, each step independently committable, ending with the full test/type/lint gate.
