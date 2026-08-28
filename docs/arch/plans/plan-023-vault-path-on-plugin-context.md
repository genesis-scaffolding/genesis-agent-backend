# Plan 023: `vault_path` on `PluginContext`

Implements [ADR-023](../adr-023-vault-path-on-plugin-context.md). Phase 1 of the ComfyUI rollout; lands first so the service can see the vault.

## Working rules

- Branch: `feature/comfyui-service` off `main` (shared branch for all three ADRs).
- One commit per plan.
- Verification gate:
  ```
  uv run pytest -q
  uv run pyright
  uv run ruff check genesis_worker
  ```

---

## Step 1 — Amend `genesis_worker/contracts/context.py`

Move `vault_path: Path` from `SourceContext` to `PluginContext`. Drop the explicit declaration on `SourceContext`; the base field is inherited.

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
    vault_path: Path                           # NEW
    secrets: SecretsAccessor = field(default_factory=NoSecretsAccessor)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceContext(PluginContext):
    local_path: Path = field(default_factory=Path)
    # vault_path inherited
```

Confirm during the change that no in-tree caller constructs `SourceContext` positionally — both `_factories.py` and `SourceRegistry` use kwargs. Document the field order in a comment if any positional caller slips in later.

## Step 2 — Update `genesis_worker/registries.py`

Add `_common_kwargs(cls)` on `_Registry`:

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

Rewrite `SourceRegistry._context()` and `ServiceRegistry._context()` to spread `**self._common_kwargs(cls)`. `SourceRegistry.vault_path` (the property) is unchanged.

## Step 3 — Update `genesis_worker/tests/_factories.py`

`source_ctx` and `service_ctx` gain a `vault_path` parameter (default `<root>/vault`) and pass it through.

## Step 4 — Tests

Add `genesis_worker/tests/test_context_vault_path.py`:

- `test_plugin_context_exposes_vault_path` — construct `ServiceContext` via `service_ctx`, assert `ctx.vault_path == supplied_path`.
- `test_source_context_inherits_vault_path` — construct `SourceContext` via `source_ctx`, assert `ctx.vault_path == supplied_path`.
- `test_registries_populate_vault_path` — build a `GenesisWorker` with a fixture `Settings`, assert every constructed plugin's `_ctx.vault_path == settings.paths.resolved_vault_path`.

## Step 5 — Run gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

All must pass. Commit and pause for user approval before Phase 2.

---

## Files changed summary

| File | Change |
|---|---|
| `genesis_worker/contracts/context.py` | Lift `vault_path` to `PluginContext` |
| `genesis_worker/registries.py` | Add `_common_kwargs`; rewrite both `_context()` methods |
| `genesis_worker/tests/_factories.py` | Add `vault_path` arg to factories |
| `genesis_worker/tests/test_context_vault_path.py` | Create |

## Notes

- The contract test (`test_plugin_boundary.py`) is plugin-import-focused; contract-shape changes don't affect it.
- Any third-party plugin that built `SourceContext` positionally breaks silently with this change. The in-tree callers are verified to use kwargs; external consumers are out of scope.
