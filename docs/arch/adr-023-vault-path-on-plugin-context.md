# ADR-023: `vault_path` on `PluginContext`

## Title

`vault_path` on `PluginContext` — service plugins gain access to the model vault.

## Status

Accepted.

## Context

`PluginContext` and `SourceContext` were defined in ADR-009. The base `PluginContext` carries the framework-resolved directory bundle (`data_dir`, `config_dir`, `cache_dir`, `state_dir`, `log_dir`), `repo_root`, the plugin's options slice, and a secrets accessor. `SourceContext` extends it with `local_path` and `vault_path`.

`vault_path` was added to `SourceContext` because every source needs to know where to walk. It is the same path the framework already computes in `SourceRegistry._context()` as `self._settings.paths.resolved_vault_path`. Services do not currently see it.

Adding the ComfyUI service (ADR-025) requires `vault_path` on services: the service bind-mounts `<vault>/comfyui/` into the container as the ComfyUI models directory, and the symlink applier writes into `<vault>/comfyui/<role>/`. Without `vault_path` on the service context, the service would have to resolve the vault itself — duplicating the source registry's resolution logic, which ADR-009 explicitly forbids (extensions resolve nothing for themselves; the framework initialises extensions and passes everything they need).

The current shape is also asymmetric: `SourceContext` carries `vault_path` and `local_path`, while `ServiceContext` carries nothing beyond the base. The asymmetry encodes an unspoken assumption — "only sources know about the vault" — which ADR-025 disproves.

## Decision

We will lift `vault_path` to `PluginContext`. Both `SourceContext` and `ServiceContext` will inherit it. The framework will populate it in both registries via a shared context-builder helper.

### Contract change (`genesis_worker/contracts/context.py`)

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
    vault_path: Path   # NEW
    secrets: SecretsAccessor = field(default_factory=NoSecretsAccessor)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceContext(PluginContext):
    local_path: Path = field(default_factory=Path)
    # vault_path inherited
```

`SourceContext`'s explicit `vault_path` field is removed; the inherited base field takes its place. `ServiceContext` is unchanged in shape and now carries `vault_path` automatically.

### Registry change (`genesis_worker/registries.py`)

`_Registry` gains a `_common_kwargs(cls)` helper that returns the kwargs every `PluginContext` needs:

```python
def _common_kwargs(self, cls: type[Plugin]) -> dict:
    return {
        "name": cls.name,
        "repo_root": self._settings.paths.resolved_repo_root,
        "secrets": self._settings.secrets.accessor(),
        "vault_path": self._settings.paths.resolved_vault_path,
        **self._dirs(cls),
    }
```

`SourceRegistry._context()` and `ServiceRegistry._context()` use it:

```python
# SourceRegistry
def _context(self, cls: type[ModelSource]) -> SourceContext:
    options = self._settings.options_for("sources", cls.name)
    return SourceContext(
        local_path=self._resolve_local_path(cls, options),
        options=options,
        **self._common_kwargs(cls),
    )

# ServiceRegistry
def _context(self, cls: type[InferenceService]) -> ServiceContext:
    return ServiceContext(
        options=self._settings.options_for("services", cls.name),
        **self._common_kwargs(cls),
    )
```

`SourceRegistry.vault_path` (the property) is unchanged — it remains the source registry's canonical accessor for the resolved vault path.

### Why not a separate field on `ServiceContext` only?

Three alternatives were considered:

1. **Add `vault_path` to `ServiceContext` only.** Localises the change to services. Cost: the asymmetry between the two contexts is now load-bearing — readers must remember which context has which fields. The base context remains incomplete.
2. **A `VaultPathMixin` shared by both.** Pure ceremony. Two contexts already share most of their fields via inheritance; a mixin adds a layer without removing one.
3. **Lift to base (this decision).** One definition. Both contexts expose `ctx.vault_path`. The framework has one helper that populates it. The contract test is unaffected (it walks plugin imports, not contract shape).

### Backward compatibility

- Existing source plugins that read `ctx.vault_path` keep working: `SourceContext` inherits the field.
- Existing service plugins that do not use `ctx.vault_path` are unaffected; the field is just additional information on the context.
- Test factories (`genesis_worker/tests/_factories.py`) gain a `vault_path` argument defaulting to a sensible tmp path; tests that construct contexts explicitly must either supply it or accept the default empty `Path()`.

## Consequences

**Positive**

- The ComfyUI service (ADR-025) gets `ctx.vault_path` for free, no settings/options resolution.
- The source and service registries share one context-builder helper; the source-side `_resolve_local_path` and the service-side options lookup remain the only per-axis differences.
- Future services that need vault awareness (e.g. an A1111 / Stable Diffusion WebUI service) get the same mechanism without another contract change.

**Negative**

- A minor backwards-incompatible signature change for any caller that built `PluginContext` or `SourceContext` positionally and depended on the old field order. The existing test factory and the registry are the only known callers; both are updated in the same change.
- `SourceContext` previously named `vault_path` explicitly; reading the diff, a maintainer might wonder whether it was always inherited. A one-line comment in the field declaration mitigates this.

**Neutral**

- The contract test (`test_plugin_boundary.py`) walks plugin imports, not context shape. Unaffected by this change.
- `vault_path` resolution stays in `PathsSettings.resolved_vault_path`; no settings-layer changes.

## Plan

[plan-023-vault-path-on-plugin-context](../plans/plan-023-vault-path-on-plugin-context.md)
