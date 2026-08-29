# ADR-019: User recipe overlay for llama-swap

## Title
Layered recipe store: bundled recipes + optional user overlay, with force reload.

## Context

`LlamaSwapService` builds its `RecipesStore` from exactly one path: `opts.recipes_path or BUNDLED_RECIPES_PATH`, where the bundled copy ships inside the plugin (`services/llama_swap/data/recipes.yaml`). Consequences:

- Recipe support for a model family is developer-only. When a new LLM family needs a recipe, the user must edit the source tree; there is no supported on-disk location where the user's own recipes live.
- `RecipesStore.reload()` exists but is never called anywhere. Even if the user could edit a recipes file, edits would not reach the running worker until the service is re-constructed.
- Provenance in the config pipeline is first-class (`FieldSource` on `EvaluatedConfig.provenance`, `resolved_from` written into `config.yaml`), but nothing records *which file* a recipe came from, because today all recipes come from one file.

User state for this service already lives next to the generated config: `overrides.yaml` sits beside `config.yaml` (ADR-007). A recipe overlay fits the same location.

## Decision

We will load recipes from two sources, in order:

1. The bundled `data/recipes.yaml`, always.
2. An optional user overlay, `<config_path's parent>/recipes.yaml` — beside `overrides.yaml`. The existing `recipes_path` option is repurposed as the overlay's location.

Merge rules:

- **Recipe-level, keyed by name.** An overlay recipe with the same name as a bundled recipe replaces it wholesale; a new name is added. No field-level merging — same convention the bundled file already documents ("the recipe's whole sampling dict REPLACES the default's").
- **`default`:** an overlay `default` entry replaces the bundled `default` entirely.
- **No deletion.** There is no mechanism to remove a bundled recipe.
- A missing overlay is silently skipped. A present overlay that fails YAML or schema parsing raises at load time, naming the offending file — a silently-broken overlay is worse than a loud one.

Provenance: `Recipe` gains `source: str = "bundled"` (values `bundled` / `override`), set at load/merge time, mirroring the existing `name`-as-a-field. It rides the existing plumbing — `Recipes.resolve()`, `walk_models`, `EvaluatedConfig.matched_recipe` — with no new data path.

Force reload: `LlamaSwapService.reload_recipes()` re-reads both sources and refreshes the in-memory store. The Recipes view UI page gets a "↻ Reload recipes" button, placed and behaving like the config editor's "↻ Regenerate config". Reload affects the in-memory store only; it does not regenerate `config.yaml` and does not touch the running llama-swap process — regeneration stays an explicit, separate action.

The Recipes view page is restructured to mimic the config editor page: sources listed at the top in load order, the reload button below them, recipes in a bordered container with one expander per recipe, each labelled with a `bundled` / `override` source badge rendered by a `_badge()` helper in the shape of the config editor's `FieldSource` mapping.

## Status
Accepted.

## Consequences

Positive:
- Users can add recipe support for new model families (or correct a bundled one) without touching the source tree or re-merging.
- Edit-then-reload loop in a running worker: no service restart needed.
- Provenance of recipe origin flows through the resolver for free; a future config-editor "recipe (override)" badge is a one-line `_badge` change when wanted.

Negative:
- `recipes_path` option changes semantics from "replace the entire store" to "locate the overlay; bundled is always loaded first". Dev/test escape hatch that pointed at a full replacement file breaks; the two existing tests asserting replace semantics are updated.
- One more file to understand in the recipe story; the merge rules must stay documented or the overlay becomes a black box.

Neutral:
- A user cannot delete a bundled recipe, only shadow it (e.g. an overlay recipe with a `match` that no model names).
- Relative `chat_template_file` paths in overlay recipes still resolve against the bundled data directory (`_resolve_chat_template_file`); overlay authors who ship their own template files must use absolute paths.
- Two same-named recipes in bundled vs overlay is not an error; the overlay silently wins. That is the feature and the footgun in one.

## Supersedes

Partially supersedes ADR-009 (§ Config and recipes locations): "Recipes … are shipped content, not user configuration" is amended — bundled recipes remain shipped content, but user recipe configuration now exists in the data directory. Annotated inline in ADR-009.


