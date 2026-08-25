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
