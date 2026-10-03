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

    # Status is fetched once per page render. We don't wrap this in a
    # fragment: ``render_service_controls`` may render the inline install
    # flow, which creates its own polling fragment. Nested fragments
    # confuse Streamlit's placeholder reservation during long docker
    # pulls (the worker thread blocks in ``subprocess.run`` for minutes
    # with zero state updates, while the outer fragment keeps rerunning
    # every 2s against the unchanged inner state — the page goes white).
    # Same trade-off as the llama-swap status page: the install flow's
    # internal fragment triggers a full app rerun on terminal, which
    # auto-switches the button from Install → Start. The Start → Running
    # transition is observed by manually reloading the page.
    render_service_controls(svc, worker.service_status(SERVICE_NAME), key_prefix="status-comfyui")

    st.divider()

    st.subheader("Container info")
    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**Image:** `{svc.image_ref}`")
        st.markdown(f"**Container name:** `{svc._options.container_name}`")
        st.markdown(f"**Listen:** `{svc.listen_address}`")
    with cols[1]:
        nvidia_state = "available" if svc.has_nvidia_gpu else "not detected"
        amd_state = "available" if svc.has_amd_gpu else "not detected"
        st.markdown(f"**NVIDIA GPU (host):** {nvidia_state}")
        st.markdown(f"**AMD GPU (host):** {amd_state}")
        variant = svc.effective_gpu_variant
        variant_present = svc.has_nvidia_gpu if variant == "cuda" else svc.has_amd_gpu
        if svc._options.gpu_required and not variant_present:
            vendor = "NVIDIA" if variant == "cuda" else "AMD"
            st.warning(
                f"No {vendor} GPU on this host (active variant: {variant}). "
                "Switch the GPU variant below or set `gpu_required: false` "
                "in the service options to allow starting without GPU."
            )
        public_host = svc.public_host()
        st.markdown(f"**Public URL:** `http://{public_host}:{svc._options.listen_port}/`")


# --- GPU variant (ADR-040) -------------------------------------------------
# Mirrors llama_swap's variant selectbox: an explicit choice wins over
# the snapshot auto-pick (NVIDIA -> cuda, else AMD -> rocm). In-memory
# only; a worker restart reverts to auto-pick.
def _on_variant_change() -> None:
    raw = st.session_state["status-comfyui-variant"]
    svc.set_gpu_variant(None if raw == "auto" else raw)


with st.container(border=True):
    st.subheader("GPU variant")
    variant_labels = ["auto", "cuda", "rocm"]
    current_variant = svc.gpu_variant or "auto"
    st.selectbox(
        "Image variant",
        variant_labels,
        index=variant_labels.index(current_variant),
        key="status-comfyui-variant",
        on_change=_on_variant_change,
    )
    st.caption(
        f"Active: **{svc.effective_gpu_variant}** — "
        f"image `{svc.image_ref}`. Takes effect on the next start."
    )

# --- Console ---------------------------------------------------------------
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, n_bytes=8 * 1024, key="comfyui")
