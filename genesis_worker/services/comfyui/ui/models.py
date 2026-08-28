"""Symlink management page for the ComfyUI service.

Lists current symlinks (read from ``model_symlink.yaml``), lets the
user add new ones via a multi-step form, and offers a "Prune dangling"
action for cleanup.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from genesis_worker.services.comfyui.symlinks import SymlinkApplier, SymlinkRow

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)
applier: SymlinkApplier = svc.symlinks

st.title("Models")

st.caption(
    f"Manage symlinks under `{svc._vault_models_dir}/<role>/` that point at "
    f"files in the vault. Persisted in `{svc._symlinks_file}`."
)

# Standard ComfyUI role subdirs.
COMFYUI_ROLES = [
    "checkpoints",
    "diffusion_models",
    "loras",
    "vae",
    "controlnet",
    "t2i_adapter",
    "clip",
    "unet",
    "style_models",
    "upscale_models",
    "embeddings",
    "hypernetworks",
]

# Session-state keys for the add form.
_FORM_STATE_KEY = "models/add_form_state"
_DIALOG_OPEN_KEY = "models/add_dialog_open"


def _refresh() -> None:
    """Re-render the table from the on-disk yaml."""
    st.session_state.pop("models/rows_cache", None)
    st.rerun()


def _current_rows() -> list[SymlinkRow]:
    catalog = worker.catalog()
    return applier.list_current(catalog)


# --- current symlinks table -----------------------------------------------

st.subheader("Current symlinks")
rows = st.session_state.get("models/rows_cache") or _current_rows()
st.session_state["models/rows_cache"] = rows

if not rows:
    st.caption("No symlinks yet. Use **Add symlinks** below to create one.")
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


# --- add dialog -----------------------------------------------------------


@st.dialog("Add symlinks")
def _add_dialog() -> None:
    catalog = worker.catalog()
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
                        COMFYUI_ROLES,
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
                st.success(f"Added {len(selections)} row(s)")
                _refresh()
    with cols[1]:
        if st.button("Cancel", key=f"{_FORM_STATE_KEY}/cancel"):
            st.rerun()


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


# --- open dialog ----------------------------------------------------------

st.divider()
if st.button("Add symlinks", key="models/add_btn"):
    _add_dialog()
