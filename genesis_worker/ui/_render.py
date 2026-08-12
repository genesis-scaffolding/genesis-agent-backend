"""Renderers for catalog entities in the UI.

Kept separate from the page scripts so search/filter views and the
pi-export view can reuse the same layout.
"""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import ModelEntry


def format_bytes(n: int) -> str:
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


def render_entry(entry: ModelEntry) -> None:
    """Render one catalog entry as a structured, scannable layout.

    Layout: summary header, then a piece table (filename / role / size),
    then notes and any source-specific metadata, then the on-disk
    directory. The pieces are already sorted by role by the walker, so
    main files appear above config files.
    """
    st.markdown(f"**Source:** {entry.source}")
    st.markdown(
        f"**Total:** {format_bytes(entry.total_bytes)} "
        f"({len(entry.pieces)} files)"
    )

    if entry.pieces:
        rows = [
            {
                "File": p.filename,
                "Role": p.role,
                "Size": format_bytes(p.bytes),
            }
            for p in entry.pieces
        ]
        st.dataframe(rows, hide_index=True, width="stretch")

    if entry.notes:
        st.markdown("**Notes**")
        for note in entry.notes:
            st.markdown(f"- {note}")

    if entry.extra:
        extras = ", ".join(f"`{k}`={v}" for k, v in entry.extra.items())
        st.markdown(f"**Metadata:** {extras}")

    st.caption(f"Directory: `{entry.directory}`")
