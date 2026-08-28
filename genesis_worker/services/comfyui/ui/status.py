"""Landing page for the ComfyUI service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import ServiceState
from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")

    # Auto-refresh while the service is in a non-terminal transition
    # (starting / stopping). The render_service_controls helper
    # disables the action button in those states; the fragment
    # re-polls the worker's status so the user sees the transition
    # from STARTING to RUNNING without manually reloading the page.
    @st.fragment(run_every="2s")
    def _service_info() -> None:
        status = worker.service_status(SERVICE_NAME)
        render_service_controls(svc, status, key_prefix="status-comfyui")

        # While transitioning, show a one-line "what's happening" hint.
        if status.state == ServiceState.STARTING:
            st.caption("Container is starting - ComfyUI may take 30-60s to become reachable.")
        elif status.state == ServiceState.STOPPING:
            st.caption("Container is stopping...")

    _service_info()

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
