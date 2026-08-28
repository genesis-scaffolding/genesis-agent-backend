"""Image management page for the ComfyUI service. Stub — fleshed out in plan-025 3.4."""

from __future__ import annotations

import streamlit as st

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title("Image")
st.caption("Installable version picker — full page lands in plan-025 3.4.")
