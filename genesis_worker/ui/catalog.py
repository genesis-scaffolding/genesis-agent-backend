"""Read-only catalog browse view."""

from __future__ import annotations

import streamlit as st

worker = st.session_state["worker"]

st.header("Catalog")

if st.button("↻ Rescan", key="catalog-rescan"):
    worker.rescan_catalog()
    st.rerun()

catalog = worker.catalog()
if not catalog.entries:
    st.info("Catalog is empty.")
    st.stop()

for entry in catalog.entries:
    with st.expander(f"{entry.name}  ({entry.source})"):
        st.code(str(entry), language="yaml")