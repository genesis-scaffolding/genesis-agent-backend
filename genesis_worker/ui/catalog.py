"""Read-only catalog browse view."""

from __future__ import annotations

import streamlit as st

worker = st.session_state["worker"]

st.header("Catalog")

if st.button("↻ Rescan", key="catalog-rescan"):
    worker.rescan_catalog()
    st.rerun()

catalog = worker.catalog()
by_source = catalog.by_source()
total = sum(len(v) for v in by_source.values())
if total == 0:
    st.info("Catalog is empty.")
    st.stop()

for source, entries in by_source.items():
    for entry in entries:
        with st.expander(f"{entry.name}  ({source})"):
            st.code(str(entry), language="yaml")