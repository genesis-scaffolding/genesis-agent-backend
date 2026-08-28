"""Symlink management page for the ComfyUI service.

Lists current symlinks (read from ``model_symlink.yaml``), lets the
user add new ones via a multi-step form, and offers a "Prune dangling"
action for cleanup.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from genesis_worker.services.comfyui.service import (
    _COMFYUI_ROLES as _COMFYUI_ROLES_STATIC,
)
from genesis_worker.services.comfyui.symlinks import SymlinkApplier, SymlinkRow

_COMFYUI_ROLES_STATIC = list(_COMFYUI_ROLES_STATIC)  # convert tuple to list for concatenation
from genesis_worker.utils.process import DockerContainer

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)
applier: SymlinkApplier = svc.symlinks
catalog = worker.catalog()

st.title("Models")

st.caption(
    "Symlink model weights from the catalog into specific subdirectories "
    "(e.g. ``checkpoints``, ``loras``, ``text_encoder``) within the ComfyUI "
    "model vault. Files are **not copied** — the vault entry remains the single "
    "source of truth and ComfyUI accesses it via a symlink. "
    "Changes are persisted in ``model_symlink.yaml`` and synced to disk immediately."
)

# Standard ComfyUI model subdirectories (static baseline).


# Session-state keys for the add form.
_FORM_STATE_KEY = "models/add_form_state"
_DIALOG_OPEN_KEY = "models/add_dialog_open"


def _discover_roles(container_name: str) -> list[str]:
    """List model subdirectories the running ComfyUI container exposes.

    Runs ``ls /opt/comfyui/app/models/`` inside the container. Returns
    directories only. Falls back to the static list on any failure.
    """
    container = DockerContainer(container_name)
    if not container.is_running():
        return _COMFYUI_ROLES_STATIC
    rc, stdout, _ = container.exec_run(["ls", "/opt/comfyui/app/models/"])
    if rc != 0:
        return _COMFYUI_ROLES_STATIC
    discovered = sorted({line.strip() for line in stdout.splitlines() if line.strip()})
    # Merge: static list as baseline, discovered dirs appended so new ones appear at the bottom.
    known = set(_COMFYUI_ROLES_STATIC)
    return _COMFYUI_ROLES_STATIC + [d for d in discovered if d not in known]


def _refresh() -> None:
    """Re-render the table from the on-disk yaml."""
    st.session_state.pop("models/rows_cache", None)
    st.rerun()


def _current_rows() -> list[SymlinkRow]:
    return applier.list_current(catalog)


# --- add dialog -----------------------------------------------------------


@st.dialog("Add symlinks")
def _add_dialog() -> None:
    roles = _discover_roles(svc._options.container_name)
    by_source = catalog.by_source()

    if not by_source:
        st.warning("No catalog entries with at least one weight piece.")
        return

    source = st.selectbox("Source", sorted(by_source.keys()), key=f"{_FORM_STATE_KEY}/source")
    assert isinstance(source, str)
    entries = sorted(by_source[source], key=lambda e: e.name)
    if not entries:
        st.warning(f"No entries from {source!r}.")
        return

    selected_entries = st.multiselect(
        "Entries",
        entries,
        format_func=lambda e: e.name,
        key=f"{_FORM_STATE_KEY}/entries",
    )

    # For each selected entry, render piece checkboxes + role dropdowns.
    selections: list[dict[str, str]] = []
    if selected_entries:
        st.divider()
        st.write("Pieces to symlink")
        for entry in selected_entries:
            weight_pieces = [p for p in entry.pieces if p.role != "config"]
            if not weight_pieces:
                st.caption(f"_{entry.name}_ — no weight pieces, skipping")
                continue
            st.markdown(f"**{entry.name}**")
            for piece in weight_pieces:
                cols = st.columns([1, 3])
                with cols[0]:
                    enabled = st.checkbox(
                        Path(piece.filename).name,
                        key=f"{_FORM_STATE_KEY}/{entry.name}/{piece.filename}/enabled",
                    )
                with cols[1]:
                    role = st.selectbox(
                        "Role",
                        roles,
                        key=f"{_FORM_STATE_KEY}/{entry.name}/{piece.filename}/role",
                        label_visibility="collapsed",
                    )
                if enabled:
                    selections.append(
                        {
                            "source": source,
                            "entry": entry.name,
                            "piece": Path(piece.filename).name,
                            "target_subdir": role,
                        }
                    )

    cols = st.columns(2)
    with cols[0]:
        if st.button("Add", key=f"{_FORM_STATE_KEY}/submit", disabled=not selections):
            errors = applier.add(selections)
            if errors:
                for err in errors:
                    st.error(err)
            else:
                result = applier.apply(catalog)
                if result.errors:
                    for row, msg in result.errors:
                        st.error(f"{row.symlink_path}: {msg}")
                if result.created or result.updated:
                    n = len(result.created) + len(result.updated)
                    st.success(f"Added {len(selections)} row(s); {n} symlink(s) synced to disk")
                else:
                    st.success(f"Added {len(selections)} row(s)")
                _refresh()
    with cols[1]:
        if st.button("Cancel", key=f"{_FORM_STATE_KEY}/cancel"):
            st.rerun()


# --- current symlinks table -----------------------------------------------

st.subheader("Current symlinks")
rows = st.session_state.get("models/rows_cache") or _current_rows()
st.session_state["models/rows_cache"] = rows

if not rows:
    st.caption("No symlinks yet.")
else:
    table_data = [
        {
            "Source": r.source,
            "Entry": r.entry,
            "Piece": r.piece,
            "Target subdir": r.target_subdir,
            "Symlink path": str(r.symlink_path),
            "Resolves to": str(r.target_path) if r.target_path else "— (missing)",
        }
        for r in rows
    ]
    st.dataframe(table_data, use_container_width=True, hide_index=True)

    # Per-row delete.
    st.divider()
    st.subheader("Remove")
    delete_labels = [
        f"{r.source}/{r.entry} → {r.target_subdir}/{Path(r.piece).name}"
        for r in rows
    ]
    delete_idx = st.selectbox(
        "Select row to remove (yaml entry only; prune dangling to clean disk)",
        range(len(rows)),
        format_func=lambda i: delete_labels[i],
        key="models/delete_idx",
    )
    if st.button("Remove selected row", key="models/delete_btn"):
        applier.remove([rows[delete_idx]])
        _refresh()

# Action buttons — always visible.
st.divider()
sync_cols = st.columns([1, 1, 4])
with sync_cols[0]:
    if st.button("Add symlinks", key="models/add_btn"):
        _add_dialog()
with sync_cols[1]:
    if st.button("Resync to disk", key="models/sync_all_btn"):
        result = applier.apply(catalog)
        if result.errors:
            for row, msg in result.errors:
                st.error(f"{row.symlink_path}: {msg}")
        n_created = len(result.created)
        n_updated = len(result.updated)
        if n_created or n_updated:
            st.success(f"Synced: {n_created} created, {n_updated} updated")
        else:
            st.info("All symlinks already on disk.")
        _refresh()




# --- prune action ---------------------------------------------------------

st.divider()
st.subheader("Prune dangling")
st.caption(
    "Walks `<vault>/comfyui/` for symlinks whose target no longer exists, "
    "removes them from disk and from the yaml."
)
confirm = st.checkbox(
    "I understand this deletes dangling symlinks from disk",
    key="models/prune_confirm",
)
if st.button(
    "Prune dangling",
    key="models/prune_btn",
    disabled=not confirm,
):
    result = applier.prune_dangling()
    if result.removed:
        st.success(f"Removed {len(result.removed)} dangling symlink(s)")
        _refresh()
    else:
        st.info("Nothing to prune.")



