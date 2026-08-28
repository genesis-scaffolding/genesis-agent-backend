"""Landing page for the ComfyUI service. Stub — fleshed out in plan-025 3.4."""

from __future__ import annotations

import streamlit as st

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)
st.caption("ComfyUI service — full Status page lands in plan-025 3.4.")
