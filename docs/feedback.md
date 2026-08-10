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

---

## [srv] Missing _base.py and LlamaSwapService per spec-001

**File:** `genesis_worker/services/` (package-level)

**Problem:** Per spec-001's layout, the services package should have:

```
services/
├── _base.py               # InferenceService protocol, dataclasses
├── _registry.py           # exists
└── llama_swap/
    ├── service.py         # LlamaSwapService class
    ├── lifecycle.py       # tmux + curl lifecycle
    ├── agent_export.py    # pi-models.json emission
    ├── recipes.py         # exists (Phase 3)
    ├── config.py          # exists (Phase 4)
    └── overrides.py       # exists (Phase 4)
```

But `services/_base.py` and `llama_swap/service.py` are **completely missing**.

### What `_base.py` should contain (per spec-001 + plan-002):

- **Dataclasses:** `ServiceState`, `ServiceCapabilities`, `ServiceResourceEstimate`, `ServiceStatus`, `StartResult`, `StopResult`
- **Protocol:** `InferenceService` with methods for start/stop/status/capabilities

### What `llama_swap/service.py` should contain (per plan-002, Phase 5):

- **`LlamaSwapService`** class implementing `InferenceService` — the actual wrapper around llama-swap that exposes the lifecycle (start, stop, status), capabilities (GPU, memory, quantization support), and health-checking.

The code currently has the config/recipe/override layer (Phase 3–4) but never got to the service lifecycle layer (Phase 5) or the base protocol. The llama-swap service is essentially "half-built" — it can generate config but doesn't have a class that orchestrates the running server.

---

## [svc] build_config hardcodes source keys instead of iterating catalog dynamically

**File:** `genesis_worker/services/llama_swap/config.py`

**Problem:** `build_config()` hardcodes the two known source types:

```python
for source_key in ("huggingface", "lmstudio"):
    for entry in getattr(catalog, source_key, []):
```

It reads entries from `catalog.huggingface` and `catalog.lmstudio` by name. If a new source is added (e.g. ModelScope, Civitai) the catalog gains a new attribute like `catalog.modelscope` but `build_config` never visits it. Config generation silently skips it.

**Suggested fix:** Instead of hardcoding source names, `build_config` should iterate over the catalog in a source-agnostic way. Options:

1. **Catalog exposes a unified accessor:**
   ```python
   # In Catalog schema:
   def by_source(self) -> dict[str, list[ModelEntry]]:
       ...  # returns {"huggingface": [...], "lmstudio": [...], ...}
   ```
   Then `build_config` does:
   ```python
   for source_key, entries in catalog.by_source():
       for entry in entries:
```

2. **Build a per-source lookup inside `build_config`:**
   ```python
   source_map: dict[str, list[ModelEntry]] = {}
   for entry in catalog.huggingface:
       source_map.setdefault("huggingface", []).append(entry)
   # ... but this is basically the same as #1, just computed at runtime
   ```

Either way, the list of sources should come from the catalog itself, not from a hardcoded tuple. This keeps config generation from silently ignoring new model sources.

---

## [cat] schema.py: Catalog hardcodes source fields

**File:** `genesis_worker/catalog/schema.py`

**Problem:** The `Catalog` class has hardcoded fields:

```python
class Catalog(BaseModel):
    root: str
    generated_at: str
    huggingface: list[ModelEntry] = Field(default_factory=list)
    lmstudio: list[ModelEntry] = Field(default_factory=list)
```

Adding a third source (e.g. ModelScope) requires modifying this class — the schema itself is not open to new sources. The `source` field on `ModelEntry` is present but unused by `Catalog`'s structure; all entries from one source land in one hardcoded attribute.

**Suggested fix:** Change `Catalog` to store entries by source dynamically:

```python
class Catalog(BaseModel):
    root: str
    generated_at: str
    sources: dict[str, list[ModelEntry]] = Field(default_factory=dict)

    def by_source(self, name: str) -> list[ModelEntry]:
        return self.sources.get(name, [])
```

Or keep backwards-compat properties:

```python
    @property
    def huggingface(self) -> list[ModelEntry]:
        return self.sources.get("huggingface", [])
```

This way the schema is open for new sources, existing code accessing `.huggingface` still works, and downstream consumers can iterate `sources.keys()` to discover all available sources.

---

## [cat] build.py: _build_catalog and _override_source_local_path both hardcode sources

**File:** `genesis_worker/catalog/build.py`

**Problem:** Two functions duplicate the same hardcoded-source assumption:

```python
# _build_catalog:
by_source: dict[str, list[ModelEntry]] = {"huggingface": [], "lmstudio": []}
# ... then later:
return Catalog(
    ...
    huggingface=by_source.get("huggingface", []),
    lmstudio=by_source.get("lmstudio", []),
)

# _override_source_local_path:
if source.name == "huggingface":
    source._local_path = vault_path / "huggingface" / "hub"
elif source.name == "lmstudio":
    source._local_path = vault_path / "lmstudio" / "models"
```

Both are blind to new sources. `_override_source_local_path` especially — a new source gets `None` passed as its local path, causing it to walk the wrong directory or fail `is_available()`.

**Suggested fix:**

1. **`_build_catalog`** — if `Catalog` is changed to a dynamic `sources` dict (see above), this becomes trivial: just insert into the dict, no pre-seeding needed.

2. **`_override_source_local_path`** — instead of hardcoding source→path mappings, use a convention or a registry lookup. Options:

   - **Convention-based:** Sources declare their vault subdirectory as a class attribute:
     ```python
     class HuggingFaceSource:
         vault_subdir = "huggingface/hub"
     ```
     Then the override just concatenates: `vault_path / source.vault_subdir`

   - **Registry-based:** The source registry maps each source name to its vault path prefix.

   This makes adding a new source a one-line change (add the class + register it) instead of requiring a second edit in `_override_source_local_path`.