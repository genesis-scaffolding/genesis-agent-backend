"""Pi-agent config export: preview, download, install."""

from __future__ import annotations

import json

import streamlit as st

worker = st.session_state["worker"]
svc = worker.service("llama_swap")

st.title("Pi export")
st.caption("Export the pi-agent config (models.json) for llama-swap.")

if st.button("Preview", key="pi-preview"):
    data = svc.export_for_agent()
    st.session_state["pi_preview"] = data

preview = st.session_state.get("pi_preview")
if preview is not None:
    st.code(json.dumps(preview, indent=2), language="json")
    st.download_button(
        "Download models.json",
        data=json.dumps(preview, indent=2),
        file_name="models.json",
        mime="application/json",
        key="pi-download",
    )

st.divider()
st.subheader("Install")
target = svc.agent_config_target()
st.write(f"Target: `{target}`")

if st.button(f"Install to {target}", key="pi-install"):
    ok = svc.write_agent_config(target)
    if ok:
        st.success(f"written to {target}")
    else:
        st.info("already up to date")
