"""Read-only catalog browse view with vault summary and acquisition."""

from __future__ import annotations

import streamlit as st

from genesis_worker.ui._nav import to_relative as _to_relative
from genesis_worker.ui._render import format_bytes, render_entry

worker = st.session_state["worker"]

st.title("Model Catalog")

# --- Section 1: vault summary + refresh -------------------------------------
with st.container(border=True):
    st.header("Vault Info")

    catalog = worker.catalog()
    catalog_by_source = catalog.by_source()
    sources = worker.list_sources()
    vault = worker.settings.paths.resolved_vault_path

    missing = "  — MISSING" if not vault.exists() else ""
    st.markdown(f"**Path:** `{vault}`{missing}")

    total_entries = sum(len(v) for v in catalog_by_source.values())
    total_bytes = sum(e.total_bytes for entries in catalog_by_source.values() for e in entries)
    st.markdown(f"**Total:** {total_entries} models, {format_bytes(total_bytes)}")

    for info in sources:
        entries = catalog_by_source.get(info.name, [])
        if not entries:
            st.markdown(f"- **{info.display_name}**: 0 models")
            continue
        bytes_ = sum(e.total_bytes for e in entries)
        st.markdown(f"- **{info.display_name}**: {len(entries)} models, {format_bytes(bytes_)}")

    st.divider()

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
        new_total = sum(len(v) for v in result.by_source().values())
        st.session_state["catalog_rescanning"] = False
        st.toast(f"Found {new_total} entries")
        st.rerun()


# --- Section 2: acquire + model list ----------------------------------------
with st.container(border=True):
    st.header("Models")

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
                st.switch_page(_to_relative(worker.source(choice_info.name).ui_pages[0].path))

    st.divider()

    if total_entries == 0:
        st.info("Catalog is empty. Acquire a model or check your vault path.")
    else:
        tab_labels = [s.display_name for s in sources]
        tabs = st.tabs(tab_labels) if tab_labels else []

        for tab, source in zip(tabs, sources, strict=True):
            with tab:
                entries = catalog_by_source.get(source.name, [])
                if not entries:
                    st.caption("No entries from this source.")
                    continue
                for entry in entries:
                    label = f"{entry.name}  ({format_bytes(entry.total_bytes)})"
                    with st.expander(label):
                        render_entry(entry)
