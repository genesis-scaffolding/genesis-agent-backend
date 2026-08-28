"""Landing page for the ComfyUI service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")
    render_service_controls(svc, worker.service_status(SERVICE_NAME), key_prefix="status-comfyui")

    st.divider()

    st.subheader("Container info")
    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**Image:** `{svc.image_ref}`")
        st.markdown(f"**Container name:** `{svc._options.container_name}`")
        st.markdown(f"**Listen:** `{svc.listen_address}`")
    with cols[1]:
        gpu_state = "available" if svc.has_nvidia_gpu else "not detected"
        st.markdown(f"**GPU (host):** {gpu_state}")
        if svc._options.gpu_required and not svc.has_nvidia_gpu:
            st.warning(
                "No NVIDIA GPU on this host. Set `gpu_required: false` in "
                "the service options to allow starting without GPU."
            )
        public_host = svc.public_host()
        st.markdown(f"**Public URL:** `http://{public_host}:{svc._options.listen_port}/`")

# --- Console ---------------------------------------------------------------
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, n_bytes=8 * 1024, key="comfyui")
