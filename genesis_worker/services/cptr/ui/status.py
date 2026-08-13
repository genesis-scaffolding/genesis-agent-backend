"""Landing page for the cptr service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.utils.ui._install_flow import render_inline_install

SERVICE_NAME = "cptr"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")

    status = worker.service_status(SERVICE_NAME)
    if status.state.value == "running":
        st.badge("Running", color="green")
    else:
        st.badge("Stopped", color="gray")

    if status.state.value == "running":
        if st.button("Stop", key="status-stop"):
            worker.stop_service(SERVICE_NAME)
            st.rerun()
    elif not svc.is_available():
        installable = svc.primary_installable()
        if installable is not None:
            render_inline_install(installable, key_prefix="status-cptr")
        else:
            st.caption("Not installed")
    else:
        if st.button("Start", key="status-start"):
            worker.start_service(SERVICE_NAME)
            st.rerun()

    endpoint = svc.web_ui_endpoint()
    if status.state.value == "running" and endpoint:
        st.link_button("Open Web UI", endpoint)

    st.divider()

    st.subheader("Configuration")
    st.markdown(f"**Listen:** `{svc.listen_address}`")
    installed = svc.installed_version or "—"
    st.markdown(f"**Installed version:** `{installed}`")


# --- Console ---------------------------------------------------------------
# Live tail of the lifecycle log file. The fragment reruns on its own
# schedule so the rest of the page stays stable while the user clicks
# around.
with st.container(border=True):
    st.subheader("Console")

    @st.fragment(run_every="2s")
    def _console() -> None:
        content = svc.tail_log(8 * 1024)
        if content:
            st.code(content, language=None)
        else:
            st.caption("No log output yet.")

    _console()