"""Streamlit service controls — badge, Start/Stop, inline install, and Web UI link."""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import InferenceService, ServiceStatus
from genesis_worker.utils.ui._install_flow import render_inline_install


def render_service_controls(
    svc: InferenceService,
    status: ServiceStatus,
    *,
    show_web_ui_link: bool = True,
    key_prefix: str = "",
) -> None:
    """Render service info: state badge, Start/Stop, inline install, Web UI link.

    ``key_prefix`` namespaces Streamlit widget keys to avoid collisions when
    multiple instances appear on the same page.

    Assumes the caller has already fetched ``worker.service_status(name)`` and
    holds it in ``status``. Reads ``svc.is_available()`` and
    ``svc.web_ui_endpoint()`` through the contract interface.

    The block is intentionally uncontainered so callers can wrap it in their
    own layout. Use ``with st.container(border=True):`` at the call site for
    a bordered appearance.
    """
    worker = st.session_state["worker"]
    name = svc.name

    if status.state.value == "running":
        st.badge("Running", color="green")
    else:
        st.badge("Stopped", color="gray")

    if status.state.value == "running":
        if st.button("Stop", key=f"{key_prefix}-stop"):
            worker.stop_service(name)
            st.rerun()
    elif not svc.is_available():
        installable = svc.primary_installable()
        if installable is not None:
            render_inline_install(installable, key_prefix=f"{key_prefix}-install")
        else:
            st.caption("Not installed")
    else:
        if st.button("Start", key=f"{key_prefix}-start"):
            worker.start_service(name)
            st.rerun()

    if show_web_ui_link:
        endpoint = getattr(svc, "web_ui_endpoint", lambda: None)()
        if status.state.value == "running" and endpoint:
            st.link_button("Open Web UI", endpoint)
