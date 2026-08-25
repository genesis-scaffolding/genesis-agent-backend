"""View the merged recipe store: bundled + user override file, with reload.

Layout mirrors config_editor.py: sources at top, a reload button, one
expander per recipe.
"""

from __future__ import annotations

import streamlit as st
import yaml

from genesis_worker.services.llama_swap.recipes import Recipe

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)


# ---------------------------------------------------------------------------
# Helpers (defined first so the page loop can call them by name)
# ---------------------------------------------------------------------------


def _badge(source: str) -> str:
    return {"bundled": "bundled", "override": "override"}.get(source, "bundled")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("Recipes")
st.caption("Bundled recipes + your override file. Reload to pick up file edits.")

for source, path in svc.recipe_sources:
    note = "" if source == "bundled" or path.exists() else "  (not present)"
    st.markdown(f"`{source}: {path}`{note}")

reload_key = "reload-recipes-view"
if st.button("↻ Reload recipes", key=reload_key):
    svc.reload_recipes()
    st.success("Reloaded")
    st.rerun()

st.divider()
with st.container(border=True):
    st.subheader("Add new recipe")
    st.caption(
        "Create a recipe override. The overlay replaces bundled recipes wholesale per name. "
        "Use `match` for family detection; omit for default recipe. Sampling dict replaces default entirely."
    )
    new_name = st.text_input("Recipe name", key="new-recipe-name")
    template = (
        'match: ""\n'
        'binary: null\n'
        'sampling:\n'
        '  temp: 0.8\n'
        '  top_p: 0.95\n'
        '  top_k: 40\n'
        '  min_p: 0.0\n'
        '  presence_penalty: 0.0\n'
        '  repeat_penalty: 1.0\n'
        'chat_template_file: null\n'
        'chat_template_kwargs: {}\n'
        'parallel: null\n'
        'spec: null\n'
        'kv_cache: null\n'
        'mmproj_offload: false\n'
        'ctx_min: null\n'
        'reasoning_budget: 0\n'
        'reasoning_budget_message: ""\n'
    )
    new_yaml = st.text_area(
        "Recipe YAML body",
        value=template,
        key="new-recipe-yaml",
        height=240,
    )
    if st.button("Add recipe", key="add-recipe"):
        if not new_name:
            st.error("Name is required")
        else:
            try:
                parsed = yaml.safe_load(new_yaml) or {}
                if not isinstance(parsed, dict):
                    raise ValueError("YAML root must be a mapping")
                svc.save_recipe_override(new_name, parsed)
                st.success(f"Recipe {new_name} added")
                st.rerun()
            except Exception as exc:
                st.error(f"Add failed: {exc}")

recipes = svc.list_recipes()
items: list[tuple[str, Recipe]] = []
if recipes.default is not None:
    items.append(("default", recipes.default))
items.extend(("matchable", r) for r in recipes.matchable)
if not items:
    st.info("No recipes found.")
    st.stop()

with st.container(border=True):
    st.subheader("Recipes")
    for kind, recipe in items:
        label = f"{recipe.name}  ({kind})  ({_badge(recipe.source)})"
        with st.expander(label):
            st.code(
                yaml.safe_dump(recipe.model_dump(exclude={"source"}), sort_keys=False),
                language="yaml",
            )
            with st.expander("Override / Edit"):
                st.caption("Edit the recipe body. Saving writes the whole recipe to the overlay file, replacing the bundled version.")
                default_yaml = yaml.safe_dump(
                    recipe.model_dump(exclude={"source", "name"}), sort_keys=False
                )
                edit_yaml = st.text_area(
                    "Recipe YAML",
                    value=default_yaml,
                    key=f"edit-{recipe.name}",
                    height=240,
                )
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Save override", key=f"save-{recipe.name}"):
                        try:
                            parsed = yaml.safe_load(edit_yaml) or {}
                            if not isinstance(parsed, dict):
                                raise ValueError("YAML root must be a mapping")
                            svc.save_recipe_override(recipe.name, parsed)
                            st.success("Saved")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Save failed: {exc}")
                with col2:
                    if st.button("Delete override", key=f"del-{recipe.name}"):
                        try:
                            svc.delete_recipe_override(recipe.name)
                            st.success("Override removed")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Delete failed: {exc}")
