# Feedback / Issues

_Authored by: Coding Agent_

## [src] _base.py: ModelPiece / DiscoveredModel co-mingled with ModelSource

**File:** `genesis_worker/sources/_base.py`

**Problem:** `ModelPiece` and `DiscoveredModel` are schema / entity objects — plain data structures that describe *what* a model looks like. `ModelSource` (the `Protocol`) is the base interface for *where* models come from. These are conceptually distinct and currently co-mingled in the same file.

**Suggested fix:** Hoist `ModelPiece` and `DiscoveredModel` out of the `sources` package into a dedicated schema layer, e.g. `genesis_worker/catalog/schema.py` (the catalog schema module is already referenced in the docstring) or a new `genesis_worker/models.py` right under the `genesis_worker` package. Keep only the `ModelSource` protocol in `sources/_base.py`.

---

## [src] _base.py: dataclass instead of Pydantic

**File:** `genesis_worker/sources/_base.py`

**Problem:** The codebase already uses Pydantic (`pydantic-settings`, `pydantic.BaseModel`) throughout. `ModelPiece` and `DiscoveredModel` are plain `@dataclass` classes. These objects are created by parsing external input (filesystem layouts, directory scans, etc.), which is exactly the kind of scenario where Pydantic's validation, coercion, and default-handling shine.

**Suggested fix:** Convert `ModelPiece` and `DiscoveredModel` to Pydantic `BaseModel` subclasses. This gives:
- Input validation (e.g. ensure `bytes` is non-negative)
- Type coercion (e.g. string → `Path`)
- Built-in `.model_dump()` / `.model_validate()` for serialization round-trips
- Consistency with the rest of the project's data classes

---

## [src] _registry.py: No facade, no settings passthrough

**File:** `genesis_worker/sources/_registry.py`

**Problem:** The registry is just a bare dict of `name → class`. Two issues:

1. **No facade / lifecycle object.** `all_sources()` returns freshly-instantiated classes on every call with no way to control when bootstrap happens. Downstream callers have no object to work with — they either call `all_sources()` or import the module and hope bootstrap ran.

2. **No settings passthrough.** Each concrete source (e.g. `HuggingFaceSource`) independently calls `xdg_path()` to resolve its own paths. But `Settings` already has `sources.huggingface.local_path` defined. The source should *receive* these settings, not re-resolve them internally. This duplicates logic and makes config overrides fragile.

**Suggested fix:**

Introduce a `SourceRegistry` facade class (or function) that:
- **Bootstraps on construction** — imports all sibling modules and populates the registry
- **Creates source instances** during bootstrap, not on every call
- **Receives a `Settings` object** and passes the relevant sub-settings (`settings.sources.huggingface`, `settings.sources.lmstudio`, etc.) to each source's constructor
- **Exposes utility methods:**
  - `.get(name: str) → ModelSource` — lookup by name
  - `.all() → list[ModelSource]` — list all sources

```python
class SourceRegistry:
    def __init__(self, settings: Settings):
        self._registry: dict[str, ModelSource] = {}
        # bootstrap + instantiate with settings

    def get(self, name: str) -> ModelSource:
        ...

    def all(self) -> list[ModelSource]:
        ...
```

Concrete sources would change from:

```python
# Current: each source calls xdg_path() itself
class HuggingFaceSource:
    def __init__(self):
        self._local_path = xdg_path("DATA", ".local/share") / "huggingface"
```

To:

```python
# Proposed: settings passed in from the registry
class HuggingFaceSource:
    def __init__(self, source_settings: HuggingFaceSourceSettings):
        self._local_path = source_settings.local_path or xdg_path("DATA", ".local/share") / "huggingface"
```

This centralizes the wiring, eliminates duplicated path resolution, and makes the dependency flow explicit.

---

## [src] Source paths: duplicated xdg_path + vault prefix, no relative-path support

**Files:** `genesis_worker/sources/huggingface.py`, `genesis_worker/sources/lmstudio.py`

**Problem:** Each source repeats the same path fallback pattern:

```python
def local_path(self) -> Path:
    if self._local_path is not None:
        return self._local_path
    return xdg_path("DATA", ".local/share") / "vault" / "huggingface" / "hub"
    # or: xdg_path("DATA", ".local/share") / "vault" / "lmstudio" / "models"
```

This couples every source to:
1. `xdg_path()` — duplicate import
2. The hardcoded `"vault"` convention — duplicated across sources
3. An absolute-path default — not portable across machines

**Suggested fix:**

In `Settings`, the `local_path` fields should store **relative paths** by default:

```python
class HuggingFaceSourceSettings(BaseModel):
    local_path: str | None = None  # default: "huggingface/hub"
    default_revision: str = "main"
```

The **SourceRegistry** (proposed in the issue above) resolves the full path during construction:

```python
# Inside SourceRegistry.__init__
def _resolve_path(self, relative_path: str, settings: Settings) -> Path:
    if Path(relative_path).is_absolute():
        return Path(relative_path)
    return settings.paths.resolved_vault_path / relative_path
```

Then pass the resolved `Path` to each source:

```python
# Source receives a concrete Path, no xdg_path calls
class HuggingFaceSource:
    def __init__(self, local_path: Path) -> None:
        self._local_path = local_path  # already resolved
```

Add a comment to `SourcesSettings` explaining the convention:

```python
class HuggingFaceSourceSettings(BaseModel):
    """local_path is relative to vault_path by default. Supply an absolute path to override."""
    local_path: str | None = None  # defaults to "huggingface/hub"
```

This eliminates duplicated path logic, makes settings portable across machines, and puts the vault convention in one authoritative place (the registry).

---

## [srv] _registry.py: Same facade / settings-passthrough problem as sources

**File:** `genesis_worker/services/_registry.py`

**Problem:** The service registry is an almost exact copy of the source registry — bare `dict[str, type]`, silent `_bootstrap()`, `all_services()` creates fresh instances on every call. It has the same issues: implicit side effects on import, no lifecycle object, no settings passthrough.

The docstring even calls it a "mirror" of the source registry. Mirror, but also same smell.

**Suggested fix:** Apply the same `SourceRegistry`-style facade pattern here. A `ServiceRegistry` class that:
- Bootstraps on construction (lazy, explicit)
- Accepts `Settings` and passes relevant service settings (e.g. `settings.services.llama_swap`) to each service constructor
- Stores pre-instantiated objects, not bare classes
- Exposes `.get(name)` and `.all()` returning service instances

This could even be a shared mixin/base so both registries share the same bootstrap + instantiation logic.