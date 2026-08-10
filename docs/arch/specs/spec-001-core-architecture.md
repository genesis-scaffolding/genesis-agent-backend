# Spec 001: Core architecture — package, sources, catalog, recipes, config generation

## Goal

Implement ADR-003, ADR-004, ADR-006, ADR-007. Stand up the `genesis_worker` package skeleton; implement the model-source extension axis with HuggingFace and LM Studio; build the unified catalog service with PyYAML emit; implement the recipe schema, longest-match resolver, override store, and `config.yaml` generation with write-if-changed. End-state: a content-equivalent `config.yaml` can be generated from the new modules.

This spec covers Phases 0–4 of the master plan. The running `llama-swap` and the `bin/` scripts are untouched.

## Extension axes and the facade pattern

Both extension axes — model sources and inference services — follow the same pattern:

- A **Protocol** declares the interface a concrete class must satisfy.
- Each concrete implementation lives in its own subpackage under the axis's package, with the class in `source.py` / `service.py` and the package's `__init__.py` re-exporting it.
- A **Registry facade** (`SourceRegistry`, `ServiceRegistry`) is the single point of construction. On construction it auto-discovers every subpackage under its axis, imports each, finds the concrete class by attribute pattern, resolves any framework-level wiring (paths, settings), and instantiates it.
- A **Package-level facade** (`GenesisWorker` in `genesis_worker/facade.py`) sits on top of both registries and the catalog service. It is the single public entry point that CLI scripts, Streamlit pages, and external consumers (e.g. the orchestrator) use to drive the worker.

There is **no decorator-based registration, no explicit class list, and no module-level state** — the registries walk their packages and find new implementations automatically. Adding a new source or service is one new subpackage; the registries and the facade pick it up with no edits anywhere else.

Sources and services are **pure logic**: they declare their wiring needs (`vault_subdir` for sources; `settings` for services) and the framework resolves those needs at construction time. Sources do not import `xdg_path`; services do not read settings on their own.

The facade pattern is the keystone of the architecture. See ADR-003 for the rationale.

## Layout

```
my-agent-backend/
├── pyproject.toml
├── uv.lock
├── .python-version
├── Makefile                       # unchanged
├── bin/                           # unchanged
├── recipes.yaml                   # unchanged
├── config.yaml                    # unchanged (this spec does NOT write it; validation writes a temp copy)
├── MODEL_CATALOG.{yaml,md}        # unchanged (this spec does NOT overwrite them)
├── pi-models.json                 # unchanged
├── vendor/                        # unchanged
├── templates/                     # unchanged
├── docs/                          # new (plan + arch/)
│   ├── plan.md
│   └── arch/adr-003..008, specs/spec-001..003, plans/plan-001..003
└── genesis_worker/                # NEW
    ├── __init__.py
    ├── models.py                  # DiscoveredModel, ModelPiece (hoisted from sources; shared with future axes)
    ├── facade.py                  # GenesisWorker — single public entry point for CLI / Streamlit / external consumers
    ├── settings.py                # Phase 0
    ├── paths.py                   # Phase 0
    ├── sources/
    │   ├── __init__.py            # re-exports SourceRegistry, HuggingFaceSource, LMSource, ModelSource
    │   ├── _base.py               # ModelSource Protocol
    │   ├── _registry.py           # SourceRegistry facade
    │   ├── _classify.py           # shared classification helpers (COMPONENT_DIRS, WEIGHT_EXTS, SKIP_FILENAMES, classify, role_sort_key)
    │   ├── huggingface/
    │   │   ├── __init__.py        # re-exports HuggingFaceSource
    │   │   └── source.py          # HuggingFaceSource (Phase 1 walker; acquire in spec-002)
    │   └── lmstudio/
    │       ├── __init__.py        # re-exports LMSource
    │       └── source.py          # LMSource (Phase 1 walker)
    ├── services/
    │   ├── __init__.py            # re-exports ServiceRegistry, InferenceService, dataclasses
    │   ├── _base.py               # InferenceService Protocol + ServiceCapabilities / ServiceResourceEstimate / ServiceStatus / StartResult / StopResult / ServiceState
    │   ├── _registry.py           # ServiceRegistry facade
    │   └── llama_swap/
    │       ├── __init__.py            # re-exports LlamaSwapService, Recipe, Recipes, ResolvedRecipes, OverridesStore
    │       ├── recipes.py         # Phase 3
    │       ├── config.py          # Phase 4
    │       ├── overrides.py       # Phase 4
    │       ├── service.py         # LlamaSwapService — read-only methods real; lifecycle methods (start/stop/status/etc.) stubbed until plan-002
    │       ├── lifecycle.py       # Phase 5 (spec-002)
    │       └── agent_export.py    # Phase 6 (spec-002)
    ├── catalog_build.py           # CatalogService (takes a SourceRegistry; schema is in models.py)
    └── tests/
        ├── test_paths.py
        ├── test_settings.py
        ├── test_facade.py            # GenesisWorker facade
        ├── test_sources_registry.py   # SourceRegistry facade contract
        ├── test_sources_huggingface.py
        ├── test_sources_lmstudio.py
        ├── test_services_registry.py  # ServiceRegistry facade contract
        ├── test_catalog_build.py
        ├── test_recipes.py
        ├── test_overrides.py
        └── test_config_emit.py
```

`bin/`, `Makefile`, `recipes.yaml`, `config.yaml`, `MODEL_CATALOG.*`, `pi-models.json` are **not modified by this spec**.

## Packages & dependencies

Single-package project. Repo root is the package root.

```bash
# Bootstrap (one-time)
uv init                 # creates pyproject.toml, uv.lock, .python-version, hello.py
rm hello.py             # we don't need the default module

# Runtime deps
uv add pydantic pydantic-settings pyyaml psutil pynvml

# Dev deps
uv add --dev pytest ruff pyright
```

(`huggingface_hub`, `streamlit`, `typer` are added in spec-002 and spec-003 respectively.)

`.python-version` is set to `3.11` (matches the orchestrator and the existing PEP 723 scripts).

## Modules

### `genesis_worker/paths.py`

XDG-aware path resolver + repo-root auto-detect.

```python
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Auto-detect the repo root from this file's location.

    Walks upward from ``Path(__file__).parent`` until it finds a directory
    containing ``pyproject.toml`` or ``Makefile``. Used as the default
    location for legacy state files until they are migrated to XDG.

    Falls back to ``Path(__file__).parent`` if no marker is found (i.e.
    the package was installed as a wheel outside the source tree).
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() or (candidate / "Makefile").is_file():
            return candidate
    return here


def xdg_path(name: str, default_relative_to_home: str) -> Path:
    """XDG-compliant toolkit path.

    Honors ``$XDG_<name>_HOME`` if set; otherwise falls back to the
    canonical default relative to ``$HOME``. Appends ``genesis-worker``
    to whichever base is resolved.
    """
    base = os.environ.get(f"XDG_{name}_HOME")
    root = Path(base) if base else Path.home() / default_relative_to_home
    return root / "genesis-worker"
```

### `genesis_worker/settings.py`

```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import repo_root, xdg_path

# --- Path fields -------------------------------------------------------------


class PathsSettings(BaseModel):
    """XDG-aware path fields, with optional legacy-repo-root fallback."""

    data_dir: Path = Field(default_factory=lambda: xdg_path("DATA", ".local/share"))
    config_dir: Path = Field(default_factory=lambda: xdg_path("CONFIG", ".config"))
    cache_dir: Path = Field(default_factory=lambda: xdg_path("CACHE", ".cache"))
    state_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state"))
    log_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state"))

    vault_path: Path | None = None

    @property
    def resolved_vault_path(self) -> Path:
        if self.vault_path is not None:
            return self.vault_path
        return self.data_dir / "vault"

    @property
    def resolved_repo_root(self) -> Path:
        return repo_root()


# --- Per-source settings -----------------------------------------------------


class HuggingFaceSourceSettings(BaseModel):
    local_path: Path | None = None
    default_revision: str = "main"


class LMSourceSettings(BaseModel):
    local_path: Path | None = None


class SourcesSettings(BaseModel):
    huggingface: HuggingFaceSourceSettings = Field(default_factory=HuggingFaceSourceSettings)
    lmstudio: LMSourceSettings = Field(default_factory=LMSourceSettings)


# --- Per-service settings ----------------------------------------------------


class LlamaSwapServiceSettings(BaseModel):
    config_path: Path | None = None
    recipes_path: Path | None = None
    listen_addr: str = "127.0.0.1:8080"
    session_name: str = "swap"
    log_file: Path | None = None
    health_timeout_s: float = 60.0
    kv_quant_over_bytes: int = 25_000_000_000
    mmproj_offload_over_bytes: int = 25_000_000_000
    default_binary_rel: str = "vendor/llama.cpp/build/bin/llama-server"


class ServicesSettings(BaseModel):
    llama_swap: LlamaSwapServiceSettings = Field(default_factory=LlamaSwapServiceSettings)


# --- Top-level settings ------------------------------------------------------


class Settings(BaseSettings):
    """Runtime configuration for the Genesis Worker."""

    model_config = SettingsConfigDict(
        env_prefix="GENESIS_",
        env_nested_delimiter="__",
        env_file=("dev.env", ".env"),
        extra="ignore",
    )

    paths: PathsSettings = Field(default_factory=PathsSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    services: ServicesSettings = Field(default_factory=ServicesSettings)
```

Per-source `local_path` is `Path | None`: an absolute path overrides the default; `None` falls through to `vault_subdir`. (A future tightening to `str | None` would let users set portable relative paths — see plan-001 post-v1 notes.)

### `genesis_worker/facade.py`

The package-level facade. The single public entry point that CLI scripts, Streamlit pages, and external consumers use to drive the worker. Owns the settings, source registry, service registry, and catalog service; consumers ask the worker for what they need rather than reaching into the registries directly.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .catalog_build import CatalogService
from .models import Catalog
from .services._base import ServiceCapabilities
from .services._registry import ServiceRegistry
from .sources._registry import SourceRegistry

if TYPE_CHECKING:
    from .settings import Settings


@dataclass(frozen=True)
class SourceInfo:
    """Display-oriented view of one registered source."""

    name: str
    display_name: str
    can_acquire: bool
    is_available: bool


@dataclass(frozen=True)
class ServiceInfo:
    """Display-oriented view of one registered service."""

    name: str
    display_name: str
    capabilities: ServiceCapabilities


class GenesisWorker:
    """Top-level facade for the worker.

    Construction wires together settings, source registry, service
    registry, and catalog service. Consumers (CLI, Streamlit, tests)
    ask the worker for what they need via the public methods; they do
    not reach into the registries directly.

    Methods that depend on spec-002 (acquire flows, lifecycle plumbing,
    metrics collection) are intentionally absent here — they land in
    plan-002/3 once ``AcquireSession`` and ``LlamaSwapService`` lifecycle
    are implemented.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else _default_settings()
        self._source_registry = SourceRegistry(self._settings)
        self._service_registry = ServiceRegistry(self._settings)
        self._catalog_service = CatalogService(self._source_registry)
        self._catalog_cache: Catalog | None = None

    # --- Settings / registries (escape hatches) ----------------------------

    @property
    def settings(self) -> Settings: ...
    @property
    def sources(self) -> SourceRegistry: ...
    @property
    def services(self) -> ServiceRegistry: ...
    @property
    def catalog_service(self) -> CatalogService: ...

    # --- Catalog ------------------------------------------------------------

    def rescan_catalog(self) -> Catalog:
        """Re-walk the vault and return the unified catalog. Updates the cache."""
        self._catalog_cache = self._catalog_service.rescan()
        return self._catalog_cache

    def catalog(self) -> Catalog:
        """Return the most recently scanned catalog, scanning on first call."""
        if self._catalog_cache is None:
            self._catalog_cache = self._catalog_service.rescan()
        return self._catalog_cache

    # --- Inspection (for UI / CLI listings) ---------------------------------

    def list_sources(self) -> list[SourceInfo]: ...
    def list_services(self) -> list[ServiceInfo]: ...


def _default_settings() -> Settings:
    from .settings import Settings as _Settings
    return _Settings()
```

Typical usage from a CLI script or Streamlit page:

```python
from genesis_worker import GenesisWorker

worker = GenesisWorker()
for info in worker.list_sources():
    print(info.display_name, "available" if info.is_available else "missing")
for info in worker.list_services():
    print(info.display_name, info.capabilities.can_serve_llm)
catalog = worker.rescan_catalog()
```

The facade's `__init__.py` re-exports `GenesisWorker`, `SourceInfo`, and `ServiceInfo` so the import line stays short.

### `genesis_worker/models.py`

Hoisted from `sources/_base.py` so the entity types live at the framework level, not inside one extension axis. The `ModelSource` Protocol still imports `DiscoveredModel` from here; future axes (e.g. ComfyUI, AIToolkit) that emit discoveries use the same dataclasses.

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ModelPiece:
    """One file in a model directory."""

    role: str  # "main", "mmproj", "mtp", "transformer", "vae", "config"
    filename: str
    path: Path
    bytes: int


@dataclass(frozen=True)
class DiscoveredModel:
    """One model as discovered by a source."""

    source: str  # "huggingface", "lmstudio"
    native_id: str  # "org/repo" or "publisher/model-dir"
    pieces: list[ModelPiece]
    total_bytes: int
    directory: Path
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
```

These are intentionally frozen dataclasses, not Pydantic models. They are built up in walker loops and consumed in-memory; they are never serialized to JSON/YAML (the catalog's `ModelEntry` is the YAML-facing representation).

### `genesis_worker/sources/_base.py`

```python
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import DiscoveredModel


@runtime_checkable
class ModelSource(Protocol):
    """One kind of model repository.

    Concrete sources declare:

    - ``name``: short identifier (``"huggingface"``, ``"lmstudio"``).
    - ``display_name``: human-readable name for UI.
    - ``can_acquire``: whether ``AcquireSession`` is implemented (spec-002).
    - ``vault_subdir``: subdirectory under ``vault_path`` where this source's
      models live (``"huggingface/hub"``, ``"lmstudio/models"``). The
      framework uses this to default ``local_path`` when settings don't
      override it.
    - ``local_path``: the resolved path the framework assigned at
      construction. Sources do not compute this themselves.

    The framework constructs each source with ``local_path=<resolved>`` at
    registry-init time (see ``SourceRegistry``).
    """

    name: str
    display_name: str
    can_acquire: bool
    vault_subdir: str
    local_path: Path

    def is_available(self) -> bool: ...
    def walk(self) -> Sequence[DiscoveredModel]: ...
```

### `genesis_worker/sources/_registry.py`

The single point of construction for sources. Auto-discovers every subpackage under `genesis_worker.sources`, imports each, finds the concrete `ModelSource` class, and instantiates it with a resolved `local_path`. No explicit class list, no decorator, no module-level state.

```python
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

from ._base import ModelSource

if TYPE_CHECKING:
    from ..settings import Settings


def _find_extension_class(module, *required_attrs: str) -> type | None:
    """Find the first class in ``module`` that declares every required attribute."""
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if not isinstance(attr, type):
            continue
        if not all(hasattr(attr, a) for a in required_attrs):
            continue
        return attr
    return None


class SourceRegistry:
    """Facade for constructing and looking up ModelSource instances.

    On construction, walks the sibling subpackages of
    ``genesis_worker.sources`` and instantiates one of every concrete
    source found. Each source is constructed with ``local_path``
    resolved from settings.

    Path resolution (highest priority first):

    1. ``settings.sources.<name>.local_path`` is an absolute Path -> use as-is.
    2. ``settings.sources.<name>.local_path`` is a relative Path ->
       join with ``settings.paths.resolved_vault_path``.
    3. No override -> ``settings.paths.resolved_vault_path / source.vault_subdir``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, ModelSource] = {}
        self._discover()

    def _discover(self) -> None:
        pkg = importlib.import_module(__package__ or "")
        assert pkg.__path__ is not None
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            name = mod_info.name
            if not name or name.startswith("_"):
                continue
            sub = importlib.import_module(f"{pkg.__name__}.{name}")
            cls = _find_extension_class(sub, "name", "vault_subdir")
            if cls is None:
                continue
            self._instances[cls.name] = cls(local_path=self._resolve_path(cls))

    def _resolve_path(self, cls: type) -> Path: ...

    def get(self, name: str) -> ModelSource:
        return self._instances[name]

    def all(self) -> list[ModelSource]:
        return list(self._instances.values())

    @property
    def vault_path(self) -> Path:
        """The resolved vault root, derived from settings."""
        return self._settings.paths.resolved_vault_path
```

### `genesis_worker/sources/huggingface/`

A package, not a file. The class lives in `source.py`; the package's `__init__.py` re-exports it for ergonomic imports. `SourceRegistry` auto-discovers this package by walking `genesis_worker.sources`.

Lifted from `bin/catalog.py:walk_huggingface`. The walker logic is preserved verbatim; the output is a list of `DiscoveredModel` rather than dicts.

```python
"""HuggingFace cache walker.

Walks ``<local_path>/`` and emits one :class:`DiscoveredModel` per
``models--*`` directory. The live snapshot is read from ``refs/main``
and only that snapshot is enumerated.

The framework constructs each source with a fully-resolved
``local_path`` (see :class:`~genesis_worker.sources._registry.SourceRegistry`).
This module does not import ``xdg_path`` or compute paths itself — it
declares its on-disk layout via ``vault_subdir = "huggingface/hub"``.

Walker logic lifted from ``bin/catalog.py:walk_huggingface`` — the
behavior is identical, only the output type changes (dataclass instead
of dict). Classification helpers are shared via
:mod:`genesis_worker.sources._classify`.

ADR-003: this is one registered source. Adding another is one new
subpackage under ``genesis_worker/sources/`` — the registry picks it up.
"""

from __future__ import annotations

from pathlib import Path

from ...models import DiscoveredModel, ModelPiece
from .._classify import SKIP_FILENAMES, classify, role_sort_key


class HuggingFaceSource:
    """HuggingFace cache layout: ``<local_path>/models--org--repo/``."""

    name = "huggingface"
    display_name = "HuggingFace"
    can_acquire = True  # AcquireSession ships in spec-002
    vault_subdir = "huggingface/hub"
    local_path: Path  # framework-assigned at construction

    def __init__(self, local_path: Path) -> None:
        self.local_path = local_path

    def is_available(self) -> bool:
        return self.local_path.is_dir()

    def walk(self) -> list[DiscoveredModel]:
        hub_dir = self.local_path
        # ... lifted from bin/catalog.py:walk_huggingface ...
```

`__init__.py`:

```python
from .source import HuggingFaceSource

__all__ = ["HuggingFaceSource"]
```

`genesis_worker/sources/lmstudio/` follows the same pattern, with `vault_subdir = "lmstudio/models"` and the LM Studio walker.

Note: the source has **no** `local_path()` method, **no** `xdg_path` import, and **no** `_local_path` private storage. The framework constructs the source with the resolved `local_path`; the source stores it on `self.local_path` and uses it directly. There is no path computation in the source module.

### `genesis_worker/sources/_classify.py`

Shared classification helpers used by both walkers. Keeps constants and logic in one place so a bug fix applies to both sources.

```python
COMPONENT_DIRS: set[str] = {"text_encoder", "transformer", "unet", ...}
WEIGHT_EXTS: set[str] = {".gguf", ".safetensors", ".bin", ".pt", ".pth", ".ckpt"}
SKIP_FILENAMES: set[str] = {".gitattributes", "README.md", "LICENSE", ...}

def classify(path: Path) -> str: ...
def role_sort_key(role: str) -> tuple[int, str]: ...
```

### `genesis_worker/catalog_build.py`

The catalog build service. Walks every registered source via the source registry, merges discoveries into a :class:`~genesis_worker.models.Catalog`, and returns it. The schema types (``Catalog``, ``ModelEntry``) live at the framework level in :mod:`genesis_worker.models`; this module owns the service that produces them.

The `catalog/` package that previously held `schema.py` and `build.py` has been flattened — schema moved to `models.py`, build service moved to this top-level module. The principle: framework-level data shapes live together, services live at the level where they're used.

```python
from __future__ import annotations

from datetime import UTC, datetime

from .models import Catalog, DiscoveredModel, ModelEntry
from .sources._registry import SourceRegistry


class CatalogService:
    """Walks the vault and produces a unified catalog.

    The :class:`SourceRegistry` owns path resolution for every registered
    source; this service just walks them in order. The catalog's ``root``
    is the registry's ``vault_path``.
    """

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry = registry

    @property
    def vault_path(self) -> Path:
        return self._registry.vault_path

    def rescan(self) -> Catalog:
        discovered: list[DiscoveredModel] = []
        for source in self._registry.all():
            if source.is_available():
                discovered.extend(source.walk())
        return _build_catalog(discovered, root=str(self._registry.vault_path))


def _build_catalog(discovered: list[DiscoveredModel], *, root: str) -> Catalog:
    by_source: dict[str, list[ModelEntry]] = {"huggingface": [], "lmstudio": []}
    for d in discovered:
        entry = ModelEntry(
            name=d.native_id,
            source=d.source,
            pieces=list(d.pieces),
            total_bytes=d.total_bytes,
            directory=str(d.directory),
            notes=list(d.notes),
            extra=dict(d.extra),
        )
        by_source.setdefault(d.source, []).append(entry)
    return Catalog(
        root=root,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        huggingface=by_source.get("huggingface", []),
        lmstudio=by_source.get("lmstudio", []),
    )
```

Note: `CatalogService` takes a `SourceRegistry`, not a `vault_path`. The registry already routed each source to its resolved path; the service does not mutate source internals (the old `_override_source_local_path` pattern is gone).

### `genesis_worker/services/_base.py`

The axis definition: the `InferenceService` Protocol and the result / status / capability dataclasses that any concrete service must produce. Lives at the axis level so consumers (UI, CLI, facade) reason about services without importing a concrete implementation. Mirrors `sources/_base.py`'s role.

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ServiceState(StrEnum):
    """Coarse lifecycle state for an inference service."""

    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ServiceCapabilities:
    """What the service can do. Drives capability-driven UI — no hardcoded
    ``if service == "llama-swap"`` branches in the dashboard."""

    can_generate_config: bool
    can_export_for_agent: bool
    can_serve_llm: bool
    can_serve_image: bool
    can_train_models: bool
    has_web_ui: bool


@dataclass(frozen=True)
class ServiceResourceEstimate:
    """Rough resource budget. Advisory — tells the dashboard what to render,
    not what to enforce."""

    vram_bytes_typical: int
    vram_bytes_min: int
    cpu_cores_recommended: int


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    message: str = ""
    pid: int | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class StartResult:
    ok: bool
    message: str = ""
    pid: int | None = None


@dataclass(frozen=True)
class StopResult:
    ok: bool
    message: str = ""


@runtime_checkable
class InferenceService(Protocol):
    """One inference backend (llama-swap, ComfyUI, AIToolkit, vLLM, ...)."""

    name: str
    display_name: str

    def is_available(self) -> bool: ...
    def is_running(self) -> bool: ...
    def runtime_endpoint(self) -> str | None: ...
    def capabilities(self) -> ServiceCapabilities: ...
    def resource_estimate(self) -> ServiceResourceEstimate: ...
    def start(self) -> StartResult: ...
    def stop(self) -> StopResult: ...
    def status(self) -> ServiceStatus: ...
    def wait_ready(self, timeout_s: float) -> bool: ...
```

### `genesis_worker/services/_registry.py`

The single point of construction for inference services. Auto-discovers every subpackage under `genesis_worker.services`, imports each, finds the concrete `InferenceService` class, and instantiates it with its per-service settings slice. Mirrors `SourceRegistry`.

```python
from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..settings import Settings


def _find_extension_class(module, *required_attrs: str) -> type | None:
    """Find the first class in ``module`` that declares every required attribute."""
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if not isinstance(attr, type):
            continue
        if not all(hasattr(attr, a) for a in required_attrs):
            continue
        return attr
    return None


class ServiceRegistry:
    """Facade for constructing and looking up service instances.

    On construction, walks the sibling subpackages of
    ``genesis_worker.services`` and instantiates one of every concrete
    service found. Each service is constructed with its per-service
    settings slice (e.g. ``settings.services.llama_swap``) as the
    ``settings`` kwarg. Services whose ``name`` does not appear under
    ``settings.services`` get ``settings=None``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, Any] = {}
        self._discover()

    def _discover(self) -> None:
        pkg = importlib.import_module(__package__ or "")
        assert pkg.__path__ is not None
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            name = mod_info.name
            if not name or name.startswith("_"):
                continue
            sub = importlib.import_module(f"{pkg.__name__}.{name}")
            cls = _find_extension_class(sub, "name", "display_name")
            if cls is None:
                continue
            per_service = getattr(self._settings.services, cls.name, None)
            self._instances[cls.name] = cls(settings=per_service)

    def get(self, name: str) -> Any:
        return self._instances[name]

    def all(self) -> list:
        return list(self._instances.values())
```

### `genesis_worker/services/llama_swap/service.py`

The concrete `LlamaSwapService` — the one inference service we ship in spec-001. Mirrors the role of `sources/huggingface.py` for the services axis.

The Protocol defines the contract; the read-only methods (`is_available`, `capabilities`) are implemented now because they don't need tmux or curl. The lifecycle methods (`start`, `stop`, `status`, `is_running`, `runtime_endpoint`, `wait_ready`, `resource_estimate`) raise `NotImplementedError` with a docstring pointer to plan-002 — that's where the tmux + curl + psutil plumbing lands.

```python
from __future__ import annotations

import shutil

from ...settings import LlamaSwapServiceSettings
from .._base import (
    InferenceService,
    ServiceCapabilities,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
)


class LlamaSwapService(InferenceService):
    """Inference service for llama-swap.

    Implements the read-only surface of :class:`InferenceService` now;
    lifecycle hooks raise :class:`NotImplementedError` and are filled in by
    plan-002 with the tmux + curl plumbing.
    """

    name = "llama_swap"
    display_name = "llama-swap"

    def __init__(self, settings: LlamaSwapServiceSettings | None = None) -> None:
        self._settings = settings if settings is not None else LlamaSwapServiceSettings()

    def is_available(self) -> bool:
        """llama-swap binary on PATH and (if configured) its config file exists."""
        if shutil.which("llama-swap") is None:
            return False
        config = self._settings.config_path
        return config is None or config.is_file()

    def capabilities(self) -> ServiceCapabilities:
        """Static capability declaration. Drives dashboard tile rendering."""
        return ServiceCapabilities(
            can_generate_config=True,
            can_export_for_agent=True,
            can_serve_llm=True,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=False,
        )

    # --- Lifecycle methods (plan-002) --------------------------------------

    def resource_estimate(self) -> ServiceResourceEstimate:
        raise NotImplementedError("lands in plan-002 (psutil + pynvml)")

    def is_running(self) -> bool:
        raise NotImplementedError("lands in plan-002 (tmux)")

    def runtime_endpoint(self) -> str | None:
        raise NotImplementedError("lands in plan-002")

    def start(self) -> StartResult:
        raise NotImplementedError("lands in plan-002 (tmux + curl)")

    def stop(self) -> StopResult:
        raise NotImplementedError("lands in plan-002 (tmux)")

    def status(self) -> ServiceStatus:
        raise NotImplementedError("lands in plan-002 (tmux + curl)")

    def wait_ready(self, timeout_s: float) -> bool:
        raise NotImplementedError("lands in plan-002 (curl polling)")
```

The structural symmetry with the sources axis is now in place: Protocol + facade + concrete class, plus shared result/status/capability types. Adding a future service (ComfyUI, AIToolkit, vLLM) follows the same pattern: one new module + implementing the Protocol + adding the class to the consumer's list passed to `ServiceRegistry`.

### `genesis_worker/services/llama_swap/recipes.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class Recipe(BaseModel):
    """One recipe entry, plus the recipe's name as a field."""

    name: str
    match: str | None = None
    binary: str | None = None
    sampling: dict[str, Any] = Field(default_factory=dict)
    chat_template_file: str | None = None
    chat_template_kwargs: dict[str, Any] = Field(default_factory=dict)
    parallel: int | None = None
    spec: dict[str, Any] | None = None
    kv_cache: str | None = None
    mmproj_offload: bool | None = None
    ctx_min: int | None = None
    reasoning_budget: int | None = None
    reasoning_budget_message: str | None = None


class Recipes(BaseModel):
    """The full recipes.yaml: a default recipe plus matchable recipes."""

    default: Recipe | None = None
    matchable: list[Recipe] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Recipes":
        raw = yaml.safe_load(path.read_text())
        rec_dict = (raw or {}).get("recipes", {})
        default = None
        matchable: list[Recipe] = []
        for name, body in rec_dict.items():
            r = Recipe(name=name, **(body or {}))
            if r.match is None or not str(r.match).strip():
                default = r
            else:
                matchable.append(r)
        return cls(default=default, matchable=matchable)

    def resolve(self, model_name: str) -> ResolvedRecipes:
        """Return which recipes match this model and which keyword won.

        Substring shadowing: only the longest keyword(s) win; siblings
        sharing a keyword all emit one llama-swap entry each.
        """
        # ... longest-match resolver ...


@dataclass(frozen=True)
class ResolvedRecipes:
    """Resolver output: which recipes matched, and which keyword won.

    ``winner_recipe`` is the recipe whose cascade should be applied for
    fields not overridden by the user. The Config Editor (spec-003)
    uses ``winner_keyword`` to render "from recipe: <name>" badges in the UI.
    """

    matched: list[Recipe]
    winner_keyword: str
    winner_recipe: Recipe | None
```

### `genesis_worker/services/llama_swap/overrides.py`

```python
from __future__ import annotations

from pathlib import Path

import yaml


class OverridesStore:
    """Read/write ``overrides.yaml``.

    Missing file = empty store. Removing a field from overrides.yaml
    clears that override (no tombstone needed).
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        if not self.path.is_file():
            return {}
        raw = yaml.safe_load(self.path.read_text()) or {}
        return raw.get("entries", {})

    def save(self, entries: dict[str, dict]) -> None:
        payload = {"entries": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(payload, sort_keys=False))
```

### `genesis_worker/services/llama_swap/config.py`

Lifts `bin/build-config.py` logic verbatim. Replaces hand-rolled YAML emit with `yaml.dump` (ADR-006). Adds `resolved_from: <recipe_name>` annotation to each emitted entry. Iterates the catalog via `Catalog.by_source()` for source-agnostic build.

```python
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ...models import Catalog, ModelEntry
from ...paths import repo_root
from .recipes import Recipe


# Repo-root resolution so recipes can use paths like "vendor/llama.cpp/build/bin/llama-server".
REPO_ROOT = repo_root()

# Resource policy thresholds (bytes). When a model's weight size exceeds
# one of these, the corresponding VRAM-saver flag is added. These are
# machine-dependent (would change if you swapped GPUs), not model-
# dependent, so they live in code rather than in recipes.yaml.
DEFAULT_KV_QUANT_OVER = 25_000_000_000
DEFAULT_MMPROJ_OFFLOAD_OVER = 25_000_000_000
DEFAULT_BINARY_REL = "vendor/llama.cpp/build/bin/llama-server"


@dataclass(frozen=True)
class BuildThresholds:
    kv_quant_over: int = DEFAULT_KV_QUANT_OVER
    mmproj_offload_over: int = DEFAULT_MMPROJ_OFFLOAD_OVER
    default_binary_rel: str = DEFAULT_BINARY_REL


def _opt(recipe, default_recipe, key): ...
def _resolve_binary(binary: str) -> str: ...

def _is_llm_candidate(entry: ModelEntry, source: str) -> bool:
    """Skip non-LLMs: image-gen / adapters / safetensors-only HF / empty.

    Source-agnostic default (``any non-config piece``) covers future
    sources without code change.
    """
    if any("no model weights on disk" in n for n in entry.notes):
        return False
    if not entry.pieces:
        return False
    if source == "huggingface":
        return any(p.filename.lower().endswith(".gguf") for p in entry.pieces)
    return any(p.role not in ("config",) for p in entry.pieces)


@dataclass(frozen=True)
class DetectedFiles:
    main: Path | None
    mmproj: Path | None
    draft: Path | None
    is_mtp: bool
    weight_bytes: int


def detect_files(entry: ModelEntry) -> DetectedFiles: ...


def build_cmd(
    recipe: Recipe,
    files: DetectedFiles,
    *,
    default_recipe: Recipe | None = None,
    binary_override: str | None = None,
    thresholds: BuildThresholds | None = None,
    overrides: dict[str, Any] | None = None,
) -> str: ...


def make_entry_id(name, recipe, *, multi_match, all_ids, source) -> str: ...
def make_display_name(name, recipe, multi_match) -> str: ...


def build_config(
    catalog: Catalog,
    recipes,
    overrides: dict[str, dict] | None = None,
    *,
    binary_override: str | None = None,
    thresholds: BuildThresholds | None = None,
) -> list[tuple[str, dict]]:
    """Walk the catalog via ``catalog.by_source()``, match recipes, apply
    overrides, emit entries. ``recipes`` is a :class:`Recipes` object
    (the ``default`` and ``matchable`` lists live there)."""
    overrides = overrides or {}
    thresholds = thresholds or BuildThresholds()
    entries: list[tuple[str, dict]] = []
    all_ids: set[str] = set()

    for source_key, entries_for_source in catalog.by_source().items():
        for entry in entries_for_source:
            if not _is_llm_candidate(entry, source_key):
                continue
            # ... match recipes, apply overrides, emit ...
    return entries


class _LiteralBlock(str):
    """Marker subclass that triggers PyYAML's literal-block representer."""


def emit_payload(entries, root, generated_at) -> dict: ...
def write_config(path, entries, *, root, generated_at) -> bool:
    """Write iff content differs. Returns True iff a write happened."""
    ...
```

## Tests

Each test file uses pytest; the project root pytest config picks them up via `uv run pytest`.

- `test_paths.py`: `repo_root()` returns a directory containing `Makefile` or `pyproject.toml`.
- `test_settings.py`: instantiate `Settings()` with no env vars, assert defaults; set `GENESIS_PATHS__DATA_DIR=/custom/x` via `monkeypatch.setenv`, assert it wins; set `XDG_DATA_HOME=/custom/y`, assert `xdg_path("DATA", ".local/share")` returns `/custom/y/genesis-worker`.
- `test_facade.py`: `GenesisWorker()` builds settings, registries, and catalog service end-to-end. `list_sources()` / `list_services()` return display info. `rescan_catalog()` walks the vault; `catalog()` caches the result. Construction accepts an explicit `Settings` and routes it through the registries.
- `test_sources_registry.py`: `SourceRegistry(Settings())` auto-discovers both `huggingface/` and `lmstudio/` subpackages and returns both sources; `get("huggingface")` / `get("lmstudio")` work; `get("does_not_exist")` raises `KeyError`. Path-resolution contract: default → `vault_subdir`, explicit absolute → used as-is, explicit relative → joined to `vault_path`.
- `test_sources_huggingface.py`: walk against a fixture tree (build a temp HF layout under `tmp_path` with `models--org--name/refs/main`, `snapshots/<sha>/...`), assert entry count + pieces + total_bytes.
- `test_sources_lmstudio.py`: walk against a fixture `<publisher>/<model-dir>` tree, assert entry count.
- `test_services_registry.py`: `ServiceRegistry(Settings())` auto-discovers the `llama_swap/` subpackage and constructs `LlamaSwapService` with the per-service settings slice. `LlamaSwapService` satisfies the `InferenceService` Protocol; `is_available()` / `capabilities()` return real values; lifecycle methods raise `NotImplementedError` until plan-002 lands them.
- `test_catalog_build.py`: build a Catalog via `CatalogService(SourceRegistry(Settings(paths=PathsSettings(vault_path=fake_vault))))`, assert the merge. `Catalog.by_source()` returns the same data as the explicit `huggingface` / `lmstudio` fields.
- `test_recipes.py`: load current `recipes.yaml`; resolve a battery of model names; assert the winner matches `bin/build-config.py`'s behavior (qwen3.6-27b wins over qwen3.6; lfm2 → lfm2; rocinante → default; bonsai → bonsai).
- `test_overrides.py`: write/load round-trip; missing file returns `{}`; clearing a field removes it.
- `test_config_emit.py`: (a) no-overrides build → diff `cmd` strings against current `config.yaml` (byte-equal or whitespace-equal). (b) Apply an override to one entry → that entry's `cmd` reflects the override. (c) `write_config` is idempotent (mtime preserved on no-op).

## Verification (success criteria)

1. `uv sync` exits 0.
2. `uv run pytest genesis_worker/tests/` passes.
3. `uv run python -c "from genesis_worker.sources import SourceRegistry; from genesis_worker.settings import Settings; print(sorted(s.name for s in SourceRegistry(Settings()).all()))"` prints `['huggingface', 'lmstudio']`.
3a. `uv run python -c "from genesis_worker import GenesisWorker; w = GenesisWorker(); print([s.name for s in w.list_services()])"` prints at least `llama_swap`.
4. `uv run python -c "from genesis_worker.settings import Settings; print(Settings().paths)"` prints four XDG-defaulted paths.
5. `uv run python -c "from genesis_worker.settings import Settings, PathsSettings; from genesis_worker.sources import SourceRegistry; from genesis_worker.catalog_build import CatalogService; from pathlib import Path; s = Settings(paths=PathsSettings(vault_path=Path.home() / 'Data2/models')); r = SourceRegistry(s); print(CatalogService(r).rescan().huggingface[:1])"` returns at least one HuggingFace entry from the real vault.
6. `uv run python -c "from genesis_worker.services.llama_swap.config import build_config; from genesis_worker.services.llama_swap.recipes import Recipes; from pathlib import Path; ...; print(len(entries))"` shows the same entry count as `wc -l config.yaml`.
7. `uv run python -c "from genesis_worker.services.llama_swap.config import build_config; ...; yaml.safe_dump(...)" | diff - <(grep -A 1000 '^models:' config.yaml)` shows content-equivalent entries (whitespace allowed to differ).
8. `make all` still passes — `bin/catalog.py` and `bin/build-config.py` still produce their original output. The `config.yaml` on disk is untouched.
9. The running `llama-swap` (started by the existing `bin/up`) is still serving on port 8080 with the same model list.
10. `uv run ruff check`, `uv run pyright` exit 0.
