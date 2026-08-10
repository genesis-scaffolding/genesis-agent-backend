# Spec 001: Core architecture — package, sources, catalog, recipes, config generation

## Goal
Implement ADR-003, ADR-004, ADR-006, ADR-007. Stand up the `genesis_worker` package skeleton; implement the model-source extension axis with HuggingFace and LM Studio; build the unified catalog service with PyYAML emit; implement the recipe schema, longest-match resolver, override store, and `config.yaml` generation with write-if-changed. End-state: a content-equivalent `config.yaml` can be generated from the new modules.

This spec covers Phases 0–4 of the master plan. The running `llama-swap` and the `bin/` scripts are untouched.

## Layout

```
my-agent-backend/
├── pyproject.toml                 # created by `uv init`
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
    ├── facade.py                  # Phase 8 (spec-003)
    ├── settings.py                # Phase 0
    ├── paths.py                   # Phase 0
    ├── sources/
    │   ├── __init__.py
    │   ├── _base.py               # ModelSource protocol, DiscoveredModel
    │   ├── _registry.py           # @register_source, all_sources()
    │   ├── huggingface.py         # Phase 1 walker (acquire in spec-002)
    │   └── lmstudio.py            # Phase 1 walker
    ├── services/
    │   ├── __init__.py
    │   ├── _base.py               # InferenceService protocol (capabilities/resource/status)
    │   ├── _registry.py           # @register_service, all_services()
    │   └── llama_swap/
    │       ├── __init__.py
    │       ├── service.py         # Phase 5 (LlamaSwapService — spec-002)
    │       ├── recipes.py         # Phase 3
    │       ├── config.py          # Phase 4
    │       ├── overrides.py       # Phase 4
    │       ├── lifecycle.py       # Phase 5 (spec-002)
    │       └── agent_export.py    # Phase 6 (spec-002)
    ├── catalog/
    │   ├── __init__.py
    │   ├── schema.py              # Phase 2 (Catalog/ModelEntry/ModelPiece pydantic)
    │   └── build.py               # Phase 2 (CatalogService)
    └── tests/
        ├── __init__.py            # empty (or absent)
        ├── test_paths.py
        ├── test_settings.py
        ├── test_sources_registry.py
        ├── test_sources_huggingface.py
        ├── test_sources_lmstudio.py
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

from pathlib import Path


def repo_root() -> Path:
    """Auto-detect the repo root from this file's location.

    Resolves upward until it finds a directory containing either
    `pyproject.toml` or `Makefile`. Used as the default location for
    legacy state files (`recipes.yaml`, `config.yaml`, etc.) when those
    files have not yet been migrated to XDG.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() or (candidate / "Makefile").is_file():
            return candidate
    return here


def _xdg_path(name: str, default_relative_to_home: str) -> Path:
    """XDG-compliant toolkit path."""
    import os

    base = os.environ.get(f"XDG_{name}_HOME")
    root = Path(base) if base else Path.home() / default_relative_to_home
    return root / "genesis-worker"
```

### `genesis_worker/settings.py`

```python
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import _xdg_path, repo_root


class PathsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GENESIS_", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: _xdg_path("DATA", ".local/share"))
    config_dir: Path = Field(default_factory=lambda: _xdg_path("CONFIG", ".config"))
    cache_dir: Path = Field(default_factory=lambda: _xdg_path("CACHE", ".cache"))
    state_dir: Path = Field(default_factory=lambda: _xdg_path("STATE", ".local/state"))
    log_dir: Path = Field(default_factory=lambda: _xdg_path("STATE", ".local/state"))

    vault_path: Path | None = None

    @property
    def resolved_vault_path(self) -> Path:
        if self.vault_path is not None:
            return self.vault_path
        return self.data_dir / "vault"

    @property
    def resolved_repo_root(self) -> Path:
        return repo_root()


class HuggingFaceSourceSettings(BaseModel):
    local_path: Path | None = None
    default_revision: str = "main"


class LMSourceSettings(BaseModel):
    local_path: Path | None = None


class SourcesSettings(BaseModel):
    huggingface: HuggingFaceSourceSettings = HuggingFaceSourceSettings()
    lmstudio: LMSourceSettings = LMSourceSettings()


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
    llama_swap: LlamaSwapServiceSettings = LlamaSwapServiceSettings()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GENESIS_",
        env_file=("dev.env", ".env"),
        extra="ignore",
    )

    paths: PathsSettings = Field(default_factory=PathsSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    services: ServicesSettings = Field(default_factory=ServicesSettings)
```

### `genesis_worker/sources/_base.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelPiece:
    role: str  # "main", "mmproj", "mtp", "transformer", "vae", "config"
    filename: str
    path: Path
    bytes: int


@dataclass(frozen=True)
class DiscoveredModel:
    source: str
    native_id: str
    pieces: list[ModelPiece]
    total_bytes: int
    directory: Path
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@runtime_checkable
class ModelSource(Protocol):
    name: str
    display_name: str
    can_acquire: bool

    def is_available(self) -> bool: ...
    def local_path(self) -> Path: ...
    def walk(self) -> Iterable[DiscoveredModel]: ...
```

### `genesis_worker/sources/_registry.py`

```python
from __future__ import annotations

import importlib
import pkgutil
from typing import Type

from ._base import ModelSource

_REGISTRY: dict[str, Type[ModelSource]] = {}


def register_source(cls: Type[ModelSource]) -> Type[ModelSource]:
    _REGISTRY[cls.name] = cls
    return cls


def all_sources() -> list[ModelSource]:
    return [cls() for cls in _REGISTRY.values()]


def _bootstrap() -> None:
    package = __import__(__package__, fromlist=["_bootstrap"])  # genesis_worker.sources
    for mod in pkgutil.iter_modules(package.__path__):
        if mod.name.startswith("_"):
            continue
        importlib.import_module(f"{__package__}.{mod.name}")


_bootstrap()
```

### `genesis_worker/sources/huggingface.py`

Lifted from `bin/catalog.py:walk_huggingface`. The walker logic is preserved verbatim; the output is a list of `DiscoveredModel` rather than dicts.

```python
from __future__ import annotations

from pathlib import Path

from ..paths import _xdg_path
from ._base import DiscoveredModel, ModelPiece, ModelSource
from ._registry import register_source

# Constants from bin/catalog.py — preserved exactly.
COMPONENT_DIRS = {...}
WEIGHT_EXTS = {...}
SKIP_FILENAMES = {...}


@register_source
class HuggingFaceSource:
    name = "huggingface"
    display_name = "HuggingFace"
    can_acquire = True  # AcquireSession ships in spec-002

    def __init__(self, local_path: Path | None = None) -> None:
        self._local_path = local_path

    def is_available(self) -> bool:
        return self.local_path().is_dir()

    def local_path(self) -> Path:
        if self._local_path is not None:
            return self._local_path
        return _xdg_path("DATA", ".local/share") / "vault" / "huggingface" / "hub"

    def walk(self) -> list[DiscoveredModel]: ...
```

(`lmstudio.py` follows the same pattern.)

### `genesis_worker/catalog/schema.py`

```python
from __future__ import annotations

from pydantic import BaseModel, Field

from ..sources._base import ModelPiece  # re-use the dataclass


class ModelEntry(BaseModel):
    name: str
    source: str
    pieces: list[ModelPiece] = Field(default_factory=list)
    total_bytes: int
    directory: str
    notes: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class Catalog(BaseModel):
    root: str
    generated_at: str
    huggingface: list[ModelEntry] = Field(default_factory=list)
    lmstudio: list[ModelEntry] = Field(default_factory=list)
```

### `genesis_worker/catalog/build.py`

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..sources._base import DiscoveredModel, ModelPiece
from ..sources._registry import all_sources
from .schema import Catalog, ModelEntry


class CatalogService:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def rescan(self) -> Catalog:
        discovered: list[DiscoveredModel] = []
        for source in all_sources():
            if source.is_available():
                discovered.extend(source.walk())
        return self._build(discovered)

    @staticmethod
    def _build(items: list[DiscoveredModel]) -> Catalog:
        hf: list[ModelEntry] = []
        lms: list[ModelEntry] = []
        for d in items:
            entry = ModelEntry(
                name=d.native_id,
                source=d.source,
                pieces=d.pieces,
                total_bytes=d.total_bytes,
                directory=str(d.directory),
                notes=list(d.notes),
                extra=dict(d.extra),
            )
            (hf if d.source == "huggingface" else lms).append(entry)
        return Catalog(
            root="",
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            huggingface=hf,
            lmstudio=lms,
        )
```

### `genesis_worker/services/llama_swap/recipes.py`

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class Recipe(BaseModel):
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
    default: Recipe | None = None
    matchable: list[Recipe] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Recipes":
        raw = yaml.safe_load(path.read_text())
        rec_dict = raw.get("recipes", {})
        default = None
        matchable: list[Recipe] = []
        for name, body in rec_dict.items():
            r = Recipe(name=name, **body)
            if r.match is None or not str(r.match).strip():
                default = r
            else:
                matchable.append(r)
        return cls(default=default, matchable=matchable)

    def resolve(self, model_name: str) -> ResolvedRecipes: ...


@dataclass(frozen=True)
class ResolvedRecipes:
    matched: list[Recipe]  # all recipes whose keyword matched
    winner_keyword: str  # longest keyword that matched; "default" if none
    winner_recipe: Recipe  # the winning Recipe (or self.default)
```

### `genesis_worker/services/llama_swap/overrides.py`

```python
from __future__ import annotations

from pathlib import Path

import yaml


class OverridesStore:
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

Lifts `bin/build-config.py` logic verbatim. Replaces hand-rolled YAML emit with `yaml.dump` (ADR-006). Adds `resolved_from: <recipe_name>` annotation to each emitted entry.

```python
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Iterable

import yaml


SAMPLING_FLAGS = {
    "temp": "--temp",
    "top_p": "--top-p",
    "top_k": "--top-k",
    "min_p": "--min-p",
    "presence_penalty": "--presence-penalty",
    "repeat_penalty": "--repeat-penalty",
}


def build_cmd(
    recipe: Recipe,
    files: dict,
    *,
    default_recipe: Recipe | None,
    binary_override: str | None = None,
    default_binary_rel: str,
) -> str:
    # ... lifted from bin/build-config.py:build_cmd ...
    # Returns the same multi-line string with `\` continuations.
    ...


def build_config(
    catalog: Catalog,
    recipes: Recipes,
    overrides: dict[str, dict],
    *,
    binary_override: str | None = None,
    default_binary_rel: str,
) -> tuple[dict, list[str]]:
    """Return a dict suitable for yaml.dump, plus a list of `resolved_from`
    annotations parallel to the model entries (for the Config Editor UI)."""
    ...


def write_config(path: Path, payload: dict) -> bool:
    """Write iff content differs. Returns True iff a write happened."""
    text = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, width=1000)
    try:
        existing = path.read_text()
    except FileNotFoundError:
        path.write_text(text)
        return True
    if existing == text:
        return False
    path.write_text(text)
    return True
```

(`_opt`, `normalize`, `get_matching_recipes`, `_is_llm_candidate`, `detect_files`, `make_entry_id`, `make_display_name`, `build_entry` — all lifted from `bin/build-config.py`.)

## Tests

Each test file uses pytest; the project root pytest config picks them up via `uv run pytest`.

- `test_paths.py`: `repo_root()` returns a directory containing `Makefile` or `pyproject.toml`.
- `test_settings.py`: instantiate `Settings()` with no env vars, assert defaults; set `GENESIS_PATHS__DATA_DIR=/custom/x` via `monkeypatch.setenv`, assert it wins; set `XDG_DATA_HOME=/custom/y`, assert `_xdg_path("DATA", ".local/share")` returns `/custom/y/genesis-worker`.
- `test_sources_registry.py`: `all_sources()` returns at least `huggingface` and `lmstudio`; both are registered exactly once.
- `test_sources_huggingface.py`: walk against a fixture tree (build a temp HF layout under `tmp_path` with `models--org--name/refs/main`, `snapshots/<sha>/...`), assert entry count + pieces + total_bytes.
- `test_sources_lmstudio.py`: walk against a fixture `<publisher>/<model-dir>` tree, assert entry count.
- `test_catalog_build.py`: build a Catalog with two sources, assert the merge.
- `test_recipes.py`: load current `recipes.yaml`; resolve a battery of model names; assert the winner matches `bin/build-config.py`'s behavior (qwen3.6-27b wins over qwen3.6; lfm2 → lfm2; rocinante → default; bonsai → bonsai).
- `test_overrides.py`: write/load round-trip; missing file returns `{}`; clearing a field removes it.
- `test_config_emit.py`: (a) no-overrides build → diff `cmd` strings against current `config.yaml` (byte-equal or whitespace-equal). (b) Apply an override to one entry → that entry's `cmd` reflects the override. (c) `write_config` is idempotent (mtime preserved on no-op).

## Verification (success criteria)

1. `uv sync` exits 0.
2. `uv run pytest genesis_worker/tests/` passes.
3. `uv run python -c "from genesis_worker.sources import all_sources; print([s.name for s in all_sources()])"` prints `['huggingface', 'lmstudio']` (order may vary).
4. `uv run python -c "from genesis_worker.settings import Settings; print(Settings().paths)"` prints four XDG-defaulted paths.
5. `uv run python -c "from genesis_worker.catalog.build import CatalogService; from pathlib import Path; print(CatalogService(Path.home() / 'Data2/models').rescan().huggingface[:1])"` returns at least one HuggingFace entry from the real vault.
6. `uv run python -c "from genesis_worker.services.llama_swap.config import build_config; from genesis_worker.services.llama_swap.recipes import Recipes; from pathlib import Path; ...; print(len(entries))"` shows the same entry count as `wc -l config.yaml`.
7. `uv run python -c "from genesis_worker.services.llama_swap.config import build_config; ...; yaml.safe_dump(...)" | diff - <(grep -A 1000 '^models:' config.yaml)` shows content-equivalent entries (whitespace allowed to differ).
8. `make all` still passes — `bin/catalog.py` and `bin/build-config.py` still produce their original output. The `config.yaml` on disk is untouched.
9. The running `llama-swap` (started by the existing `bin/up`) is still serving on port 8080 with the same model list.
10. `uv run ruff check`, `uv run pyright` exit 0.
