# Plan 031: Built-in context size + structured pi-config export

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Field name | `ctx_size` | Matches the existing `ctx_min` — short, adjacent in schema, same shape |
| Default | `None` (no `-c` emitted) | Don't override llama-server's default unless intended |
| Cascade | override > recipe > default_recipe (no computed fallback) | Same shape as every other scalar field; `-c` is always explicit |
| Cmd emission | After `--fit-ctx`, before `--parallel` | Both belong to "runtime" section; `-c` is the explicit cap that comes after the fit floor |
| Pi resolution priority | `cfg.ctx_size` > `cfg.ctx_min` > 84000 default | When both are set, `-c` (cap) wins because it's the user's explicit decision |
| `DEFAULT_CONTEXT_WINDOW` | 84000 (was 131072) | Per Gen: 131k is too generous for a default; 84k is a more honest fallback |
| Regex path | Deleted | No round-tripping the rendered cmd; exporter reads `EvaluatedConfig` directly |
| Legacy `build_provider(config_path)` | Deleted | The structured pipeline is the only path; tests that called it are rewritten to pass `EvaluatedConfig` |
| `test_live_config_yields_field_equivalent_pi_models` | Deleted | Tested retired `config.yaml` (ADR-028); never ran on this checkout |

## Why not regex the cmd

`export_pi_config.py` previously read `config.yaml` off disk and then
regexed `--fit-ctx`, `--mmproj`, and `--chat-template-kwargs` out of
the rendered cmd string. That round-trip is brittle (every cmd-format
change risked a missed regex) and lossy (any field that didn't make it
to cmd was invisible to pi-export).

Both the cmd written to `config.yaml` and the fields emitted to
`models.json` now derive from the same `evaluate_all(...)` result.
One source of truth, no string-parsing.

## Files

### Schema + cmd emitter

- `genesis_worker/services/llama_swap/recipes.py` —
  `Recipe.ctx_size: int | None = None`
- `genesis_worker/services/llama_swap/generate_config.py` —
  `EvaluatedConfig.ctx_size: int | None = None` (defaulted field block);
  `evaluate_recipe` resolves via override > recipe > default;
  `provenance["ctx_size"]`;
  `cmd_from_evaluated_dict` emits `-c N` between `--fit-ctx` and `--parallel`
- `genesis_worker/services/llama_swap/ui/config_editor.py` —
  "Context size" row in the effective config; "Context size (cap)" override
  input paired with "Fit context"
- `genesis_worker/services/llama_swap/ui/recipes_view.py` —
  `ctx_size: null` in the new-recipe template
- `genesis_worker/services/llama_swap/data/recipes.yaml` —
  header comment block for `ctx_size`

### Pi export refactor

- `genesis_worker/services/llama_swap/export_pi_config.py` —
  rewritten: `build_provider_from_configs(configs, *, base_url, hostname)`
  and `write_models_json` only. `DEFAULT_CONTEXT_WINDOW = 84000`.
  Regex helpers (`FIT_CTX_RE`, `MMPROJ_RE`, `CHAT_TEMPLATE_KWARGS_RE`)
  and the old `build_provider(config_path)` are gone.
- `genesis_worker/contracts/service.py` —
  `export_for_agent(self, *, catalog: Catalog, base_url=None) -> dict`;
  `write_agent_config(self, target, *, catalog: Catalog, base_url=None) -> bool`
- `genesis_worker/services/llama_swap/service.py` —
  impl calls `build_provider_from_configs(self.evaluate_model_config(catalog), …)`
- `genesis_worker/services/llama_swap/ui/pi_export.py` —
  `svc.export_for_agent(catalog=worker.catalog())` /
  `svc.write_agent_config(target, catalog=worker.catalog())`
- `genesis_worker/cli/pi_models.py` —
  `data = svc.export_for_agent(catalog=worker.catalog())`

### Tests

- `genesis_worker/tests/test_generate_config.py` — 8 new tests
  covering recipe values, override cascade, default-recipe fallback,
  cmd emission, ordering, and absence-when-unset
- `genesis_worker/tests/test_agent_export.py` — rewritten: 26 tests
  around a synthetic `EvaluatedConfig` builder (`_cfg(...)`). The
  skipped live-config test is deleted. New coverage: `ctx_size`
  win-over-`ctx_min`, default-fallback (84000), empty-configs path

### Bonus hermeticity fix

- `genesis_worker/tests/test_services_registry_enable_disable.py` —
  three `disable()`-path tests now monkeypatch
  `LlamaSwapService.is_running` to False. They were flaking on
  machines where llama-swap was actually running.

## Branch + commits

Branch: `feature/llama-swap-builtin-ctx-size`

1. `feat(llama-swap): add built-in ctx_size field; emit -c N in cmd`
2. `test(registry): monkeypatch is_running in 3 tests to keep them hermetic`
3. `refactor(llama-swap): pi-export reads structured configs (no regex); default ctx 84k`

## Open follow-ups (not in this branch)

- `write_agent_config` could be marked `@abstractmethod` in the
  contract; today every service inherits the default `NotImplementedError`
- `maxTokens` is still hardcoded 16384 in pi-export; user explicitly
  chose to leave that out of scope
