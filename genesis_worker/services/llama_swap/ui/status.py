"""Landing page for the llama-swap service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.utils.ui._nav import to_relative

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.title(svc.display_name)

    status = worker.service_status(SERVICE_NAME)
    if status.state.value == "running":
        st.badge("Running", color="green")
    else:
        st.badge("Stopped", color="gray")

    if status.state.value == "running":
        if st.button("Stop", key="status-stop"):
            worker.stop_service(SERVICE_NAME)
            st.rerun()
    else:
        if st.button("Start", key="status-start"):
            worker.start_service(SERVICE_NAME)
            st.rerun()

    endpoint = svc.web_ui_endpoint()
    if status.state.value == "running" and endpoint:
        st.link_button("Open Web UI", endpoint)

    st.divider()

    st.subheader("Configuration")
    config_path = svc.config_path
    st.markdown(f"`{config_path}`")
    last_gen = svc.last_generated_at()

    if config_path.exists():
        if last_gen:
            st.write(f"✓ generated {last_gen}")
        else:
            st.write("✓ present")
    else:
        st.warning("⚠ not generated — auto-generation will read the catalog and recipes")

    cols = st.columns(2)
    with cols[0]:
        if st.button("↻ Regenerate config", key="status-regen"):
            ok = worker.regenerate_service_config(SERVICE_NAME)
            if ok:
                st.success("regenerated")
            else:
                st.info("already up to date")
            st.rerun()

    with cols[1]:
        config_editor = next(p for p in svc.ui_pages if p.label == "Config editor")
        if st.button("Manage config →", key="status-manage"):
            st.switch_page(to_relative(config_editor.path))


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
