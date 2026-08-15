"""Read-only catalog browse view with vault summary and acquisition."""

from __future__ import annotations

import streamlit as st

from genesis_worker.ui._render import format_bytes, render_entry
from genesis_worker.utils.ui._nav import to_relative as _to_relative

worker = st.session_state["worker"]

# --- Auto-refresh on acquire completion --------------------------------------
# The acquire-session registry on the worker tracks every in-flight session.
# When a new one transitions to ``complete`` since the last render, we rescan
# the catalog so the user sees their newly downloaded model without having to
# click "Rescan catalog". The set is updated after the comparison so a session
# that completes mid-render triggers exactly one toast.
last_seen_complete: set[str] = st.session_state.setdefault(
    "catalog_last_seen_complete", set()
)
current_sessions = worker.list_acquire_sessions()
newly_complete = {s["id"] for s in current_sessions if s["state"] == "complete"}
if newly_complete - last_seen_complete:
    catalog = worker.rescan_catalog()
    total = sum(len(v) for v in catalog.by_source().values())
    st.toast(f"Auto-refreshed after download — {total} models now")
last_seen_complete |= newly_complete

if "delete_done" in st.session_state:
    st.toast(f"Deleted: {st.session_state.pop('delete_done')}")

# --- Delete confirmation dialog -----------------------------------------------
if "delete_confirm" in st.session_state:
    target = st.session_state.pop("delete_confirm")

    @st.dialog("Delete model?")
    def confirm_delete():
        st.warning(f"**{target['name']}** and all its files will be permanently deleted. This cannot be undone.")
        col1, col2 = st.columns(2)
        if col1.button("Delete", key="confirm-delete-btn", type="primary"):
            worker.delete_model(target['source'], target['name'])
            st.session_state['delete_done'] = target['name']
            st.rerun()
        if col2.button("Cancel", key="cancel-delete-btn"):
            st.rerun()

    confirm_delete()

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

    if catalog.generated_at:
        st.markdown(f"**Generated:** {catalog.generated_at}")

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

    # Rescan rewrites state_dir/catalog.json when content has changed; we
    # disable the button while running so the user doesn't double-click.
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
                        if st.button(
                            "Delete",
                            key=f"delete-{entry.source}-{entry.name.replace('/', '-')}",
                        ):
                            st.session_state["delete_confirm"] = {
                                "source": entry.source,
                                "name": entry.name,
                            }
                            st.rerun()
