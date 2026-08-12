"""Read-only catalog browse view, with acquisition at the top."""

from __future__ import annotations

import streamlit as st

from genesis_worker.ui._nav import to_relative as _to_relative

worker = st.session_state["worker"]

st.header("Catalog")

# --- Acquire widget (top) ---------------------------------------------------
# Acquisition is the path to populating the catalog. Putting it above the
# list makes that flow visible: if you've already got models, the list
# below is the thing; if you don't, the button is the first thing you see.
sources = worker.list_sources()
acquirable = [s for s in sources if s.can_acquire]
if not acquirable:
    st.caption("No sources support acquisition.")
else:
    if len(acquirable) == 1:
        info = acquirable[0]
        if st.button(
            f"Get new model with {info.display_name}",
            key="catalog-acquire-go",
        ):
            st.switch_page(_to_relative(worker.source(info.name).ui_pages[0].path))
    else:
        choice_info = st.selectbox(
            "Source",
            options=acquirable,
            format_func=lambda s: s.display_name,
            key="catalog-acquire-source",
        )
        if choice_info and st.button(
            f"Get new model with {choice_info.display_name}",
            key="catalog-acquire-go",
        ):
            st.switch_page(
                _to_relative(worker.source(choice_info.name).ui_pages[0].path)
            )

st.divider()

# --- Rescan ------------------------------------------------------------------
# Rescan is a destructive-feeling action (writes to ~/.cache/genesis-worker/),
# so we disable the button while running, show a spinner, and toast the count.
rescanning = st.session_state.get("catalog_rescanning", False)
if st.button(
    "↻ Rescan catalog",
    key="catalog-rescan",
    disabled=rescanning,
):
    st.session_state["catalog_rescanning"] = True
    st.rerun()

if rescanning:
    with st.spinner("Scanning vault…"):
        result = worker.rescan_catalog()
    total = sum(len(v) for v in result.by_source().values())
    st.session_state["catalog_rescanning"] = False
    st.toast(f"Found {total} entries")
    st.rerun()

# --- Model list, one tab per source ----------------------------------------
catalog = worker.catalog()
catalog_by_source = catalog.by_source()
total = sum(len(v) for v in catalog_by_source.values())

if total == 0:
    st.info("Catalog is empty. Acquire a model or check your vault path.")
    st.stop()

tab_labels = [s.display_name for s in sources]
tabs = st.tabs(tab_labels) if tab_labels else []

for tab, source in zip(tabs, sources, strict=True):
    with tab:
        entries = catalog_by_source.get(source.name, [])
        if not entries:
            st.caption("No entries from this source.")
            continue
        for entry in entries:
            with st.expander(entry.name):
                st.code(str(entry), language="yaml")
