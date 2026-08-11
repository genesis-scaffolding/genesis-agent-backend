# ADR-009: Framework / plugin boundary

## Title
Framework / plugin boundary — `contracts` is the only shared surface

## Context
ADR-003 established sources and services as pluggable extension axes with in-tree registries. The implementation drifted: the boundary was a convention, not a structure, and both sides crossed it.

Concretely, before this ADR:

- `genesis_worker/settings.py` defined `LlamaSwapServiceSettings` — the framework owned the *plugin's* configuration schema, including `kv_quant_over_bytes` and `default_binary_rel`, knobs only llama-swap can interpret.
- `LlamaSwapService` imported that settings class and resolved its own paths from it (`repo_root` → `config_dir` → XDG fallback chains), duplicating path fields that already existed on `PathsSettings`.
- `services/llama_swap/config.py` imported `genesis_worker.paths.repo_root` and called it at module import time.
- Source plugins imported `genesis_worker.models` and `genesis_worker.sources._classify` directly.
- `ServiceRegistry` constructed plugins as `cls(settings=per_service)`, which forced every service to accept a framework settings type regardless of what it actually needed.

The registries also discovered plugins by duck-typing on attribute *names* (`hasattr(cls, "name")`), returning the first alphabetical match in `dir(module)`.

Services are not homogeneous the way sources are. Every source needs exactly one resolved `local_path`, defaultable from `vault_subdir`. llama-swap needs config, recipes, overrides and log locations; a future ComfyUI service needs entirely different ones. There is no generic per-service path resolution the framework can write, so "the framework resolves paths and passes them in" needs a mechanism.

## Decision

### Three layers

```
genesis_worker/
  contracts/     the only module plugins may import
  <framework>    settings.py  paths.py  facade.py  catalog_build.py  registries
  sources/       plugin directory
  services/      plugin directory
```

`contracts/` holds the ABCs (`ModelSource`, `InferenceService`, `AcquireSession`), every type that crosses the boundary (`DiscoveredModel`, `ModelPiece`, `Catalog`, `ModelEntry`, the service capability/status/result types, the acquire flow types), the shared `classify()` helper, and the context objects below.

`SourceInfo` and `ServiceInfo` stay framework-side: they are framework→UI view types and never reach a plugin.

### The rule

- A plugin imports from `genesis_worker.contracts` and nothing else under `genesis_worker`.
- The framework interacts with a plugin only through the ABC. It does not import plugin submodules.

Enforced by `test_plugin_boundary.py`, which AST-walks every module under `sources/` and `services/` and fails on any `genesis_worker` import outside `genesis_worker.contracts`.

### Construction: contexts

The framework resolves everything and passes one context object:

```python
@dataclass(frozen=True)
class PluginContext:
    name: str
    data_dir: Path
    config_dir: Path
    cache_dir: Path
    state_dir: Path
    log_dir: Path
    repo_root: Path
    options: Mapping[str, Any]
```

`SourceContext` adds `local_path` and `vault_path`. Per-plugin directories are scoped by `dir_name` (a class attribute defaulting to `name.replace("_", "-")`), so llama-swap's generated state lands in `<data_dir>/llama-swap/`.

### Plugins own their options schema

`Settings.sources` and `Settings.services` are `dict[str, dict[str, Any]]`. The framework carries the slice; it does not interpret it. Each plugin defines its own schema and parses `ctx.options` at construction.

`GENESIS_SERVICES__LLAMA_SWAP__LISTEN_ADDR` continues to work — pydantic-settings populates nested dicts.

### ABCs, not Protocols

`@runtime_checkable` Protocols validate method *names* only, not signatures, so `isinstance()` passed for any object with the right attribute names. ABCs enforce at instantiation, make conformance explicit at the class statement, and let the registries discover via `issubclass` instead of duck-typing.

### Config and recipes locations

- Generated config: `<data_dir>/llama-swap/config.yaml`. Overrides sit beside it.
- Recipes ship inside the plugin (`services/llama_swap/data/recipes.yaml`), resolved from `__file__`. They are shipped content, not user configuration.

This supersedes ADR-004's repo-root fallback for llama-swap's `config.yaml` and `recipes.yaml`. The repo-root copies remain in place, untouched, because `bin/` and the live llama-swap still consume them (ADR-008).

## Status
Accepted. Supersedes parts of ADR-003 (registry construction) and ADR-004 (per-service settings, repo-root fallback).

## Consequences

Positive:
- The boundary is structural and tested, not conventional.
- Adding a service no longer requires editing `settings.py`.
- Plugin path resolution is uniform: derive from the context, never from settings.
- `issubclass` discovery removes the alphabetical-duck-typing failure mode.

Negative:
- Plugin options are validated at plugin construction, not at `Settings()` construction, so a typo in `GENESIS_SERVICES__*` surfaces later and as a plugin error. An `options_schema()` hook on the ABC would let the framework validate up front and render a settings UI; deferred.
- `contracts` is a dependency magnet. Anything added there is permanent public surface for both sides.

Known exception:
- `Catalog` hardcodes `huggingface` and `lmstudio` as fields, so a framework type names two plugins. ADR-008 pins the `MODEL_CATALOG.yaml` shape for diff-validation against `bin/catalog.py`; generalizing to `entries: list[ModelEntry]` needs a custom serializer to preserve it. Revisit when `bin/catalog.py` retires.

## Spec
[spec-002-services-and-acquire](specs/spec-002-services-and-acquire.md)
