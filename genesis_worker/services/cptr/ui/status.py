"""Landing page for the cptr service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "cptr"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")
    render_service_controls(svc, worker.service_status(SERVICE_NAME), key_prefix="status-cptr")

    st.divider()

    st.subheader("Configuration")
    st.markdown(f"**Listen:** `{svc.listen_address}`")
    installed = svc.installed_version or "—"
    st.markdown(f"**Installed version:** `{installed}`")


# --- Console ---------------------------------------------------------------
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, n_bytes=8 * 1024, key="cptr")
