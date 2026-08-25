# Plan-019: User recipe overlay for llama-swap

## Step 1 — `recipes.py`: provenance field, merge, multi-path store

**File:** `genesis_worker/services/llama_swap/recipes.py`

1. `Recipe`: add `source: str = "bundled"` after `name`. Values: `"bundled"` / `"override"`. Default keeps existing `Recipe(...)` constructions and tests untouched.
2. New module-level function `merge_recipes(base: Recipes, override: Recipes) -> Recipes`:
   - start from base's `default` and `matchable`, keyed by `name`;
   - an `override` recipe with a matching name replaces the base one (wholesale); a new name is appended, preserving overlay document order;
   - `override.default` replaces `base.default` when present;
   - every recipe in the result carries the `source` of the doc it came from.
   `Recipes.load(path, source="bundled")` — single-doc load; stamps `source` on every recipe it builds (a `source` key in the YAML body is popped before construction — provenance is stamped, never user-set).
3. `RecipesStore.__init__(paths: list[Path])` — ordered; first is the base, the rest are overlays applied in order via `merge_recipes`.
   - `load()`: load each path with `Recipes.load` **only if the file exists** (missing overlay = skip; a missing *base* file is a programming error — let `Recipes.load` raise), fold the rest onto it, cache.
   - A present file that fails `yaml.safe_load` or pydantic validation raises `RuntimeError` (or let pydantic's error propagate) — the raised message must name the file path.
   - `reload()` unchanged in shape: clear cache, re-run `load()`.
4. Update module docstring line and `__all__` for `merge_recipes`.

## Step 2 — `service.py`: overlay path, reload, sources

**File:** `genesis_worker/services/llama_swap/service.py`

1. `__init__`: replace
   `self._recipes_path = opts.recipes_path or BUNDLED_RECIPES_PATH` / `RecipesStore(self._recipes_path)`
   with:
   - `self._bundled_recipes_path = BUNDLED_RECIPES_PATH`
   - `self._override_recipes_path = opts.recipes_path or self._config_path.parent / "recipes.yaml"`
   - `self._recipes = RecipesStore([self._bundled_recipes_path, self._override_recipes_path])`
2. Replace the `recipes_path` property with
   `recipe_sources -> list[tuple[str, Path]]`:
   `[("bundled", self._bundled_recipes_path), ("override", self._override_recipes_path)]`.
3. New method
   `def reload_recipes(self) -> Recipes: return self._recipes.reload()`
   (one-line docstring: re-read bundled + overlay, refresh in-memory store).
4. `list_recipes()` unchanged.

## Step 3 — `ui/recipes_view.py`: restructure to mimic `config_editor.py`

**File:** `genesis_worker/services/llama_swap/ui/recipes_view.py`

Rewrite the page; layout mirrors `config_editor.py` top-to-bottom:

1. `st.title("Recipes")` + one-line caption: bundled + overlay, picked up via reload.
2. The two source paths in backtick markdown, labelled, in load order; the override line annotated `(not present)` when the file doesn't exist.
3. `st.button("↻ Reload recipes", key="reload-recipes-view")` → `svc.reload_recipes()` → `st.rerun()` — same position/behaviour as "↻ Regenerate config".
4. `with st.container(border=True):` → `st.subheader("Recipes")` → one `st.expander` per recipe, default first then matchables, label `name  (badge)` where `_badge(source)` maps `bundled` / `override` (helper copied in shape from `config_editor._badge`).
5. Expander body: `st.code(yaml.safe_dump(recipe.model_dump(exclude={"source"}), sort_keys=False), language="yaml")` — replaces today's flat list; the `(default)` / `(matchable)` suffix is dropped since the badge covers kind only for source, so keep the existing `kind` suffix too: label is `name  (kind)  (badge)` — e.g. `qwen3.6-thinking  (matchable)  (override)`.

## Step 4 — Tests

**File:** `genesis_worker/tests/test_recipes.py`

1. `test_load_stamps_source` — `Recipes.load` stamps `source` per doc (default `"bundled"`, `"override"` when passed).
2. `test_merge_recipes_overrides_by_name_and_appends_new` — same-named overlay recipe replaces bundled wholesale (fields + `source`); new names appended in document order; base order preserved.
3. `test_merge_recipes_overlay_default_replaces` — overlay `default` wins.
4. `test_merge_recipes_keeps_base_default_when_overlay_has_none` — base `default` survives when the overlay has no default entry.
5. `test_resolve_preserves_source` — `resolve()` winner carries the doc's `source` through into `ResolvedRecipes.winner_recipe`.

**File:** `genesis_worker/tests/test_service_llama_swap.py`

1. `test_recipe_sources_default_to_bundled_then_override` — `recipe_sources` order and labels; override path == `config_path.parent / "recipes.yaml"`; bundled copy still loads first and `list_recipes().matchable` is non-empty.
2. `test_recipes_path_option_wins` (updated) — option relocates the **overlay** (bundled still loaded; both visible in `recipe_sources`).
3. `test_override_recipes_merge_over_bundled` — a written overlay recipe is visible in `list_recipes()` with `source == "override"`; overlay `default` replaces the bundled default.
4. `test_missing_override_file_is_skipped` — absent overlay = bundled recipes unchanged, all `source == "bundled"`.
5. `test_reload_recipes_picks_up_edited_override` — construct service, write overlay, `svc.reload_recipes()`, new recipe visible without re-constructing the service.
6. `test_malformed_override_recipes_raises_naming_file` — malformed overlay → `list_recipes()` raises and the message names the file.

## Step 5 — ADR-009 supersession note

**File:** `docs/arch/adr-009-framework-plugin-boundary.md`

In § "Config and recipes locations", annotate the "shipped content, not user configuration" bullet inline: amended by ADR-019 — bundled recipes remain shipped content; a user recipe overlay now lives beside `overrides.yaml`. No status change.

## Step 6 — Verify

```sh
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

All three must pass.
