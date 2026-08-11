"""Override editor for the llama-swap service."""

from __future__ import annotations

import streamlit as st

worker = st.session_state["worker"]
svc = worker.service("llama_swap")

st.header("Config editor")
st.caption("Per-model overrides. Saved to the overrides store on the service.")

catalog = worker.catalog()
by_source = catalog.by_source()
total = sum(len(v) for v in by_source.values())
if total == 0:
    st.info("No models in the catalog. Rescan from the dashboard.")
    st.stop()

for entries in by_source.values():
    for entry in entries:
        with st.expander(entry.name):
            st.code(str(entry), language="yaml")
            st.caption("Override editing ships in plan-003-chunk-3+ once recipes are migrated.")
            override = st.checkbox(
                "Override defaults", key=f"override-{entry.name}", value=False
            )
            if override:
                st.text_input("Context size", key=f"ctx-{entry.name}")
                st.text_input("KV cache quant", key=f"kvq-{entry.name}")