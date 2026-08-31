"""In-flight acquire sessions for the HuggingFace source.

Sessions are tracked centrally by the facade, so this page lists whatever
is active at the moment it renders — including sessions started in
another tab or page.
"""

from __future__ import annotations

import streamlit as st

worker = st.session_state["worker"]

st.header("Active HuggingFace acquires")

sessions = worker.list_acquire_sessions("huggingface")
if not sessions:
    st.info("No active sessions. Start one from the Acquire model page.")
    st.stop()

for s in sessions:
    with st.container(border=True):
        st.write(f"**{s['repo_id']}** — `{s['state']}`")
        if st.button("Cancel", key=f"cancel-{s['id']}"):
            worker.cancel_acquire(s["session"])
            st.rerun()
