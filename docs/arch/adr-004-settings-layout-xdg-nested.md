# ADR-004: Settings layout — XDG base dirs with nested per-source and per-service sections

## Title
Settings layout — XDG base dirs with nested per-source and per-service sections

## Context
The worker needs to organize persistent state (catalog, config files, recipes, generated exports, logs, runtime artifacts) across multiple orthogonal categories:

- User-edited declarative config (`recipes.yaml` today)
- Regenerable artifacts (`MODEL_CATALOG.{yaml,md}`, `config.yaml`, `pi-models.json` today)
- A model vault — a configurable directory the user already maintains with HF cache + LM Studio layouts
- Runtime state (tmux session, logs, lock files)

The orchestrator already adopted XDG Base Directory conventions (`genesis-infrastructure-toolkit` ADR-002). The worker follows the same convention for consistency and because power users expect XDG on Linux.

In addition, ADR-003 establishes that **sources and services are pluggable**. Each source and each service has its own settings slice (e.g., HF has a `local_path`; llama-swap has `listen_addr`, `session_name`, `kv_quant_over_bytes`). These settings must nest cleanly without forcing the framework to know about every source or service ahead of time.

For v1, the running llama-swap on this machine is consuming `config.yaml` from the repo root and `MODELS_ROOT` from `.env`. Migrating state files to XDG dirs is explicitly out of v1 (ADR-008), so the v1 defaults must point at the repo-root locations when those files exist there.

## Decision

### XDG-compliant path fields

Following the orchestrator's ADR-002 verbatim, with one addition (`vault_path`):

```python
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os


def _xdg_path(name: str, default_relative_to_home: str) -> Path:
    base = os.environ.get(f"XDG_{name}_HOME")
    root = Path(base) if base else Path.home() / default_relative_to_home
    return root / "genesis-worker"


class PathsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GENESIS_", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: _xdg_path("DATA", ".local/share"))
    config_dir: Path = Field(default_factory=lambda: _xdg_path("CONFIG", ".config"))
    cache_dir: Path = Field(default_factory=lambda: _xdg_path("CACHE", ".cache"))
    state_dir: Path = Field(default_factory=lambda: _xdg_path("STATE", ".local/state"))
    log_dir: Path = Field(default_factory=lambda: _xdg_path("STATE", ".local/state"))  # under state

    vault_path: Path | None = None  # explicit override; else resolved below

    @property
    def resolved_vault_path(self) -> Path:
        if self.vault_path is not None:
            return self.vault_path
        return self.data_dir / "vault"
```

### Env-var precedence

Identical to the orchestrator: defaults → `dev.env` → `.env` → real env vars → explicit constructor args. `GENESIS_*` is the env prefix for the worker.

### Per-source and per-service nested settings

The top-level `Settings` composes nested models:

```python
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
```

Adding a new source or service means:
1. Write the source/service's settings class.
2. Add a field on `SourcesSettings` or `ServicesSettings`.
3. Done.

> **Superseded by ADR-009.** Step 2 made the framework own each plugin's schema. Plugin
> options are now opaque to `Settings` (`sources`/`services` are `dict[str, dict[str, Any]]`);
> the plugin defines and validates its own options model. Adding a plugin no longer touches
> `settings.py` at all.

### v1 backwards-compatible path resolution

> **Superseded by ADR-009 for llama-swap.** The repo-root fallback is gone: generated
> config lands at `<data_dir>/llama-swap/config.yaml` and recipes ship inside the plugin.
> The repo-root `config.yaml` / `recipes.yaml` remain in place untouched because `bin/`
> and the live llama-swap still consume them (ADR-008) — the new code simply no longer
> reads or writes them. The text below is retained as the record of the original decision.

For paths that today live in the repo root (`recipes.yaml`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json`), the per-service and per-catalog settings have a **fallback chain**: explicit setting → repo-root path if it exists → XDG-default path. This means a fresh checkout with today's running setup works with zero config edits:

```python
def _resolve_with_repo_fallback(explicit: Path | None, repo_path: Path, xdg_default: Path) -> Path:
    if explicit is not None:
        return explicit
    if repo_path.exists():
        return repo_path
    return xdg_default
```

The `repo_root` is auto-detected from `Path(__file__)` at facade-init time and overridable via `GENESIS_REPO_ROOT`.

### Lazy directory creation

Directories are NOT created at startup. They're created on first write (`mkdir(parents=True, exist_ok=True)`). Same rationale as the orchestrator ADR-002.

## Status
Accepted; partially superseded by [ADR-009](adr-009-framework-plugin-boundary.md)
(per-plugin settings ownership, and the repo-root fallback for llama-swap).

## Consequences

Positive:
- XDG layout; consistency with the orchestrator repo.
- Per-source and per-service settings scale to N sources and N services without a central registry.
- v1 backwards-compat: zero config changes required to run the new code against the existing repo-root artifacts.
- Lazy dir creation matches prod semantics in dev.

Negative:
- Repo-root / XDG fallback chain is more code than a pure-XDG setup. Justified by the "do not break the running llama-swap" constraint.
- `dev.env` + `.env` adds two layers of file-based config to reason about. Same tradeoff as the orchestrator; same mitigation (clear precedence docs).
- The `_resolve_with_repo_fallback` helper is repeated for each state file. Acceptable; it's a small idiom.

Neutral:
- No backwards compatibility concerns beyond the running setup on this machine.

## Spec
[spec-001-core-architecture](specs/spec-001-core-architecture.md)

## Plan
[plan-001-core-architecture](plans/plan-001-core-architecture.md)
