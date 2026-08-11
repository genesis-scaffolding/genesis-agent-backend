"""Read-only view of the recipes shipped with llama-swap."""

from __future__ import annotations

import streamlit as st
import yaml

worker = st.session_state["worker"]
svc = worker.service("llama-swap")

st.header("Recipes")
st.caption("Read-only. Edits happen in the repo-root recipes.yaml during v1.")

recipes = svc.list_recipes()
if not recipes.entries:
    st.info("No recipes found.")
    st.stop()

for recipe in recipes.entries:
    with st.expander(recipe.name):
        st.code(
            yaml.safe_dump(recipe.model_dump(), sort_keys=False),
            language="yaml",
        )