"""Read-only view of the recipes shipped with llama-swap."""

from __future__ import annotations

import streamlit as st
import yaml

worker = st.session_state["worker"]
svc = worker.service("llama_swap")

st.header("Recipes")
st.caption("Read-only. Edits happen in the repo-root recipes.yaml during v1.")

recipes = svc.list_recipes()
items = []
if recipes.default is not None:
    items.append(("default", recipes.default))
items.extend(("matchable", r) for r in recipes.matchable)
if not items:
    st.info("No recipes found.")
    st.stop()

for kind, recipe in items:
    label = f"{recipe.name}  ({kind})" if kind == "matchable" else f"{recipe.name}  (default)"
    with st.expander(label):
        st.code(
            yaml.safe_dump(recipe.model_dump(), sort_keys=False),
            language="yaml",
        )