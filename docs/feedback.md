# Code Review: spec-002 chunk 3

## Feedback

### File: `genesis_worker/services/llama_swap/service.py`

#### Issue: `regenerate_config()` pulls in framework internals

The `regenerate_config()` method was reaching into three framework internals:
1. `self._worker.rescan_catalog()` — to rescan the catalog if none was passed
2. `OverridesStore(self.overrides_path()).load()` — loading overrides via the framework's store class
3. `self.config_path()` / `self.overrides_path()` — path resolution tied to framework settings

The method's stated responsibility is simply: *generate config from a catalog*. The framework details (where the catalog comes from, how overrides are stored) should stay in the framework layer (facade/Streamlit).

**Fix applied:**
- Changed signature from `regenerate_config(*, catalog=None)` to `regenerate_config(*, catalog, config_path, recipes_path, overrides=None)`
- Removed `_worker` / `bind_worker()` from the service — it only served this one method
- The facade/Streamlit is now responsible for gathering the catalog, resolving paths, and loading overrides before calling the method

**Tests updated:** `test_regenerate_config_requires_bound_worker` removed; `test_regenerate_config_writes_generated_at_and_no_longer_stale` rewritten with new signature.

**Verification:** 153 tests pass, pyright clean, 0 errors.

---

### File: `genesis_worker/services/llama_swap/agent_export.py`

#### Issue: Misleading module name

The module is named `agent_export.py` but it produces the **pi-agent** config file (`models.json` / `models.yaml`) consumed by the pi-agent runtime. The name `agent_export` is too vague — it doesn't say *which* agent, *what* format, or *what* is being exported.

Rename to **`export_pi_config.py`** (or `export_pi_models.py`) to make it clear this produces the pi-agent's config, not a generic agent export.

---

### File: `genesis_worker/services/llama_swap/config.py`

#### Issue: Misleading module name

The module is named `config.py` but its purpose is to **generate** config (build config entries from catalog + recipes + overrides, and write the output). It does not store, load, or represent configuration — it produces it.

The name `config.py` is ambiguous: it could mean "configuration data", "configuration schema", or "configuration loader". Since the module's job is generation/emission, rename it to **`generate_config.py`** (or `emit_config.py`) to make its purpose immediately clear from the import path.

---

### File: `genesis_worker/services/llama_swap/service.py`

#### Issue A: `LlamaSwapServiceSettings` imported at module level, tightly coupling the service to framework internals

The service imports `LlamaSwapServiceSettings` at the top (line 10) and uses it as a constructor parameter and throughout the module for path resolution (`config_path()`, `recipes_path()`, `overrides_path()`). This means the service module knows about the framework's settings system — it constructs a specific settings type, reads its fields, and builds paths based on settings resolution logic.

The pattern we already have with `ModelSource` is cleaner: the framework resolves everything at construction time and passes plain resolved values:

```python
# Source pattern (what we want):
source = HuggingFaceSource(local_path=resolved_path)  # plain Path
```

Instead, the service does this:

```python
# Current service pattern:
svc = LlamaSwapService(settings=LlamaSwapServiceSettings(
    config_path=..., repo_root=..., config_dir=...
))  # framework type + raw config objects
# Then in config_path():
s = self._settings
if s.config_path is not None: return s.config_path
repo_cfg = s.repo_root / "config.yaml"
...  # framework path resolution logic
```

**Desired pattern:** The framework (facade) resolves paths, builds the config entries, and passes resolved values to the service. The service receives `Path` objects and `dict`/`Catalog` objects — not a framework settings type. It should know nothing about `LlamaSwapServiceSettings`, `repo_root`, `config_dir`, etc.

---

#### Issue B: Inline module imports scattered throughout methods

There are numerous inline `from .xxx import xxx` inside method bodies:

- `from .overrides import OverridesStore` (inside `regenerate_config`)
- `from .agent_export import build_provider` (inside `export_for_agent`)
- `from .agent_export import write_models_json` (inside `write_models_json`)
- `from .agent_export import default_target_path` (inside `pi_install_target`)
- `from .config import read_generated_at` (inside `last_generated_at`)

These are used either for lazy-loading (to avoid import cycle) or ad-hoc organization, but they make the module harder to reason about and harder to test (you have to mock at runtime, not import time). **Import at the top of the file.** If there's a genuine import-cycle concern, use `TYPE_CHECKING` blocks — but the modules being imported (`config`, `agent_export`, `overrides`, `recipes`) don't import `service.py`, so there shouldn't be a cycle.

---

### File: `genesis_worker/services/llama_swap/service.py` (revisited)

#### Issue: Service should own its paths and stores as properties, not resolve from Settings

Currently the service resolves paths from `LlamaSwapServiceSettings` via methods like `config_path()` → `settings.config_path` → fallback chain. The settings system is a **framework** concern. The service should simply own the paths and stores it needs.

**Desired design:**

The constructor receives resolved values — plain `Path` objects for paths, and instantiated store objects:

```python
class LlamaSwapService(InferenceService):
    def __init__(
        self,
        config_path: Path,
        recipes_path: Path,
        overrides_path: Path,
        log_dir: Path,
    ) -> None:
        self._config_path = config_path
        self._recipes_path = recipes_path
        self._overrides_path = overrides_path
        self._log_dir = log_dir
        # Also own the store objects:
        self._recipes = Recipes(self._recipes_path)
        self._overrides = OverridesStore(self._overrides_path)
```

This mirrors the `ModelSource` pattern where the framework constructs sources with fully resolved values:

```python
# What the facade does:
svc = LlamaSwapService(
    config_path=settings.resolve_config_path(),
    recipes_path=settings.resolve_recipes_path(),
    overrides_path=config_path.parent / "overrides.yaml",
    log_dir=settings.paths.log_dir,
)
```

Then the service methods are simple property returns and direct store usage:
- `config_path` → returns `self._config_path`
- `list_recipes()` → calls `self._recipes.load()`
- `regenerate_config(catalog)` → uses `self._recipes`, `self._overrides`, `self._config_path`

No `LlamaSwapServiceSettings` dependency, no `_worker` dependency, no fallback chains inside the service. The framework's job is to resolve paths and construct stores; the service's job is to use them.

---

### File: `genesis_worker/sources/huggingface/acquire.py`

#### Issue: `huggingface_hub` import not resolvable outside the venv

Pyright reports `Import "huggingface_hub" could not be resolved` in editors that don't use the `.venv` (e.g. system Python). The package is declared in `pyproject.toml` but installed only inside the uv-managed venv. Editors using system Python (or a different venv) can't find it.

**Suggestion:** Add a `pyrightconfig.json` (or pyright settings in `pyproject.toml`) that points to the venv so the LSP can resolve the import regardless of which Python the editor happens to be using:

```json
{
  "venvPath": ".",
  "venv": ".venv"
}
```

Or ensure the LSP picks up the `.venv` via its configuration.

---

### File: `genesis_worker/sources/_base.py`

#### Issue: Acquire flow types co-mingled with ModelSource base types

`_base.py` currently holds two unrelated concerns:
1. The `ModelSource` Protocol and `DiscoveredModel` / `ModelPiece` types (the model source extension axis)
2. A whole block of acquire flow types: `AcquireFileGroup`, `AcquireProgress`, `AcquireStep`, `AcquireChoice`, `AcquireState`, `AcquireSession`

The file is named `_base.py` and its docstring says "Model source extension axis". The acquire types have nothing to do with model sources — they're part of the acquisition flow. Having them in the same file is confusing because:
- A reader opening `_base.py` to understand model sources encounters acquire types they don't need
- The file's `__all__` mixes both concerns
- The naming `_base` implies "base for sources" but acquire types are a different axis entirely

**Suggestion:** Move the acquire types to a dedicated module, e.g. `genesis_worker/sources/acquire.py`. Keep `_base.py` for source-specific types only (`ModelSource`, `DiscoveredModel`, `ModelPiece`). The acquire module becomes its own axis with its own `__all__` and its own README-level documentation.

#### Issue: `AcquireState` docstring references non-existent `GenesisWorker._acquire_sessions`

The `AcquireState` docstring says:

> *"Held by the worker (in `GenesisWorker._acquire_sessions`). The session implementation owns the transitions; the worker just persists the AcquireState across reruns."*

But `GenesisWorker` (`facade.py`) has no `_acquire_sessions` attribute. This docstring is **forward-looking** — it describes a feature that will be implemented in spec-003 (the Streamlit acquire page). Right now, acquire sessions are constructed on demand, no state tracking exists on the facade, and there's no resumption across reruns.

**Suggestion:** Either:
1. Remove the forward-looking detail until spec-003 implements it (safer — avoids confusing readers)
2. Mark it as `TODO: spec-003` so it is clear this is planned, not missing
3. Implement the `_acquire_sessions` tracking in this chunk (work ahead, but closes the gap)

---

### File: `genesis_worker/sources/huggingface/acquire.py`

#### Issue: `HfAcquireSession` does not explicitly subclass `AcquireSession`

`HfAcquireSession` implements the `AcquireSession` Protocol via duck typing — it has the right methods, so `isinstance(session, AcquireSession)` happens to work (thanks to `@runtime_checkable`). But the subclass relationship is **implicit**:

```python
class HfAcquireSession:  # No "(AcquireSession)" here
    ...
```

This means:
- A reader has to manually verify all three methods exist to know it implements the protocol
- IDE autocomplete won't show that it conforms to `AcquireSession`
- Subclassing `HfAcquireSession` doesn't automatically signal protocol conformance

**Suggestion:** Make the relationship explicit:
```python
class HfAcquireSession(AcquireSession):
    ...
```

This is purely syntactic since `AcquireSession` is a Protocol, but it makes the intent clear at a glance and helps tooling.

---

### File: `genesis_worker/sources/huggingface/source.py` (+ `__init__.py`)

#### Issue: `HuggingFaceSource` doesn't expose `HfAcquireSession`

The `HuggingFaceSource` class (the walker) and `HfAcquireSession` (the downloader) live in different files and the source doesn't expose the session class:

```python
# Current: framework has to reach inside to find HfAcquireSession
from genesis_worker.sources.huggingface.acquire import HfAcquireSession
```

The `__init__.py` does re-export `HfAcquireSession`, but from a package-level perspective — not from the `HuggingFaceSource` itself. The framework layer (`facade.py` / Streamlit app) ends up knowing the internal structure of the `huggingface` package.

This breaks the desired layering:
- **External module** (facade/Streamlit) should not have to reach inside `huggingface.acquire`
- **Framework** (the `HuggingFaceSource` walker) should not need to know about the acquire session — the facade should wire them together

**Suggestion:** Either:
1. Add a `start_acquire()` method on `HuggingFaceSource` that constructs and returns an `HfAcquireSession`, keeping the framework's knowledge at the source level: `source.start_acquire(cache_dir, revision)` → `HfAcquireSession`
2. Or make the facade construct the session directly but import from `__init__` (the re-export), not from the internal `acquire` module — this keeps the contract at the package boundary rather than the internal file

Option 1 is cleaner because it mirrors how other sources work — the source is the unit of extensibility, not the internal file layout.

---

### File: `genesis_worker/settings.py`

#### Issue A: `LlamaSwapServiceSettings` duplicates path fields from `PathsSettings`

`LlamaSwapServiceSettings` has `repo_root`, `config_dir`, and `log_dir` — fields that already exist on `PathsSettings`:

```python
class LlamaSwapServiceSettings(BaseModel):
    repo_root: Path = Field(default_factory=_default_repo_root)
    config_dir: Path = Field(default_factory=_default_config_dir)
    log_dir: Path = Field(default_factory=_default_log_dir)
```

This creates two sources of truth for the same paths. The service should not own its own copies — it should use the already-resolved values from `PathsSettings`.

**Suggestion:** Remove `repo_root`, `config_dir`, and `log_dir` from `LlamaSwapServiceSettings`. The service receives these from the facade/paths settings when it needs them. This matches the `HuggingFaceSourceSettings` / `LMSourceSettings` pattern, which only have `local_path`.

#### Issue B: `recipes_path` is hardcoded as a settings field but should come from the source code

`recipes_path` is exposed as a configurable field in `LlamaSwapServiceSettings`, but the recipes are **bundled with the service module**. They ship as part of the codebase and are versioned with the service — users don't edit them, the framework ships them.

As agreed, the service should resolve the recipes path from its own source location (`__file__`), not from settings:

```python
class LlamaSwapService:
    # Resolved at import time from the module's own location
    _RECIPE_DIR = Path(__file__).parent / "recipes"
    
    def recipes_path(self) -> Path:
        return self._RECIPE_DIR / "recipes.yaml"
```

No settings field needed. If the path needs to be overridden (for testing), inject it via the constructor.

#### Issue C: Config path defaults are wrong — should be under `data_dir/llama-swap/`

The current path resolution allows the config to land in `config_dir/` or the repo root, but we agreed that llama-swap's generated config should sit under `data_dir/llama-swap/`:

```python
# Desired default:
config_path = data_dir / "llama-swap" / "config.yaml"
```

This keeps user-modifiable config in `config_dir/` and generated/service state in `data_dir/`.