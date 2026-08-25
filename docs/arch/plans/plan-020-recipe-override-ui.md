# Plan-020: UI recipe override/add for llama-swap

## Step 1 — `recipes.py`: overlay write helper

**File:** `genesis_worker/services/llama_swap/recipes.py`

1. Keep `RecipesStore`, `Recipes`, `merge_recipes` unchanged – read-only merge engine.
2. Add `RecipesOverlayStore` for single overlay persistence:
   * `__init__(self, path: Path)` – store path only, no I/O.
   * `load(self) -> dict[str, dict]` – returns `{name: body}`; missing file → `{}`.
   * `save(self, data: dict[str, dict]) -> None` – atomic write of `{"recipes": data}`; `parent.mkdir(parents=True, exist_ok=True)`.
   * `update_recipe(self, name: str, body: dict) -> None` – validate via `Recipe(name, **body)` then save.
   * `delete_recipe(self, name: str) -> None` – remove key then save.
3. Add module helpers for service use:
   * `load_overlay_recipes(path: Path) -> dict[str, dict]`
   * `save_recipe_to_overlay(path: Path, name: str, body: dict) -> None`
   * `delete_recipe_from_overlay(path: Path, name: str) -> None`

## Step 2 — `service.py`: expose overlay write API

**File:** `genesis_worker/services/llama_swap/service.py`

1. `__init__`:
   * Keep existing `_override_recipes_path`.
   * Add `self._recipes_overlay = RecipesOverlayStore(self._override_recipes_path)`.
2. New public API:
   * `recipe_overlay_path` property → `self._override_recipes_path`.
   * `list_recipe_overrides(self) -> dict[str, Recipe]` – load overlay only, stamp `source="override"`.
   * `save_recipe_override(self, name: str, fields: dict) -> None`:
     1. Find current merged recipe by name via `self.list_recipes()`.
     2. Merge `fields` onto base to produce `full_body`.
     3. Validate via `Recipe(name, **full_body)`.
     4. `self._recipes_overlay.update_recipe(name, full_body)`.
     5. `self.reload_recipes()`.
   * `delete_recipe_override(self, name: str) -> None`:
     * `self._recipes_overlay.delete_recipe(name)`
     * `self.reload_recipes()`
3. `reload_recipes()` unchanged.

## Step 3 — `ui/recipes_view.py`: edit UI

**File:** `genesis_worker/services/llama_swap/ui/recipes_view.py`

1. Keep sources listing and reload button.
2. Per-recipe expander:
   * Header `name (kind) (badge)` via existing `_badge`.
   * Read-only YAML dump `st.code(yaml.safe_dump(...))`.
   * Nested expander "Override / Edit":
     * Caption: whole-recipe replacement, saves to overlay.
     * `st.text_area` prefilled with current merged recipe YAML excluding `source`.
     * Save button → parse YAML, call `svc.save_recipe_override(name, parsed)`, `st.success` + `st.rerun()`.
     * Delete/Clear button → `svc.delete_recipe_override(name)`, `st.success` + `st.rerun()`.
     * Errors shown via `st.error` on parse/validation failure.

## Step 4 — Behaviour guarantees

* Overlay file created on first save, parent dirs created.
* Writes atomic, validation via pydantic `Recipe`.
* `RecipesStore` remains read-only; `RecipesOverlayStore` write-only.
* No field-level merge in loader – UI builds full recipe before persisting, matching ADR-019 recipe-level merge.

## Step 5 — Verify

```sh
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

Manual checks:
* No overlay → all recipes `source=bundled`.
* Save → overlay created, badge flips to `override`, reload picks up.
* Delete → entry removed, reverts to bundled.
* Malformed YAML → `RuntimeError` naming file on next load.
