"""Landing page for the SillyTavern service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "sillytavern"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")

    # Status is fetched once per page render. We don't wrap this in a
    # fragment: ``render_service_controls`` may render the inline install
    # flow, which creates its own polling fragment. Nested fragments
    # confuse Streamlit's placeholder reservation during long docker
    # pulls. Same trade-off as the comfyui status page.
    render_service_controls(
        svc, worker.service_status(SERVICE_NAME), key_prefix="status-sillytavern"
    )

    st.divider()

    st.subheader("Container info")
    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**Image:** `{svc.image_ref}`")
        st.markdown(f"**Container name:** `{svc._options.container_name}`")
        st.markdown(f"**Listen:** `{svc.listen_address}`")
    with cols[1]:
        st.markdown(f"**Public URL:** `http://{svc.public_host()}:{svc._options.listen_port}/`")

# --- Console ---------------------------------------------------------------
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, n_bytes=8 * 1024, key="sillytavern")
