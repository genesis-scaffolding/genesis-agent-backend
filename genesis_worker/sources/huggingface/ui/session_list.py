"""In-flight acquire sessions for the HuggingFace source.

Per-page state is held in ``st.session_state[sid_key]``. There is no
facade-level session registry today (sources don't track past sessions),
so this page is a placeholder that points users at the acquire wizard.
"""

from __future__ import annotations

import streamlit as st

worker = st.session_state["worker"]

st.header("Active HuggingFace acquires")
st.caption("Active sessions are tracked per-page (in this browser session).")
st.info("Use the Acquire model page to start a new acquisition. "
        "In-flight sessions show their progress there.")

# Future: when the facade tracks sessions centrally, render them here.
sessions = worker.list_acquire_sessions("huggingface")
if sessions:
    for s in sessions:
        with st.container(border=True):
            st.write(f"**{s.get('repo_id', '?')}** — `{s.get('state', '?')}`")
            sid = s.get("id")
            if sid and st.button("Cancel", key=f"cancel-{sid}"):
                worker.cancel_acquire(sid)
                st.rerun()