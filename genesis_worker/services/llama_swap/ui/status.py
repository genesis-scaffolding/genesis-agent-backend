"""Landing page for the llama-swap service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import ServiceState

worker = st.session_state["worker"]
svc = worker.service("llama-swap")

st.header("llama-swap")

status = worker.service_status("llama-swap")
state_text = status.state.value.upper()
if status.pid:
    state_text += f"  (pid {status.pid})"
st.write(f"State: **{state_text}**")

cols = st.columns([1, 1, 2])
with cols[0]:
    if status.state == ServiceState.RUNNING and st.button("Stop", key="status-stop"):
        worker.stop_service("llama-swap")
        st.rerun()
    elif status.state == ServiceState.STOPPED and st.button("Start", key="status-start"):
        worker.start_service("llama-swap")
        st.rerun()

with cols[1]:
    endpoint = svc.runtime_endpoint()
    if status.state == ServiceState.RUNNING and endpoint:
        st.link_button("Open Web UI ↗", endpoint)

st.subheader("Configuration")
config_path = svc.config_path()
last_gen = svc.last_generated_at()

if config_path.exists():
    if last_gen:
        st.write(f"✓ generated {last_gen}")
    else:
        st.write("✓ present")
else:
    st.warning("⚠ not generated — auto-generation will read the catalog and recipes")

cols = st.columns([1, 1, 1])
with cols[0]:
    if st.button("↻ Regenerate config", key="status-regen"):
        ok = worker.regenerate_service_config("llama-swap")
        if ok:
            st.success("regenerated")
        else:
            st.info("already up to date")
        st.rerun()

with cols[1]:
    if st.button("Manage config →", key="status-manage"):
        st.switch_page("config_editor")