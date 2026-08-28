"""Streamlit service controls — badge, Start/Stop, inline install, and Web UI link."""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import InferenceService, ServiceState, ServiceStatus
from genesis_worker.utils.ui._install_flow import render_inline_install


def _render_badge(state: ServiceState) -> None:
    """State badge — colour-coded by state, with explicit intermediate states."""
    if state == ServiceState.RUNNING:
        st.badge("Running", color="green")
    elif state == ServiceState.STARTING:
        st.badge("Starting…", color="orange")
    elif state == ServiceState.STOPPING:
        st.badge("Stopping…", color="orange")
    elif state == ServiceState.FAILED:
        st.badge("Failed", color="red")
    elif state == ServiceState.UNAVAILABLE:
        st.badge("Unavailable", color="gray")
    else:  # STOPPED
        st.badge("Stopped", color="gray")


def _render_action_button(
    state: ServiceState,
    is_available: bool,
    worker,
    name: str,
    key_prefix: str,
) -> None:
    """Render the action button for the current state.

    The five states map to: Stop (RUNNING), disabled Starting… (STARTING),
    disabled Stopping… (STOPPING), inline install (!available and
    STOPPED/UNAVAILABLE), Start (available and STOPPED/FAILED/UNAVAILABLE).
    """
    if state == ServiceState.RUNNING:
        if st.button("Stop", key=f"{key_prefix}-stop"):
            worker.stop_service(name)
            st.rerun()
        return

    if state == ServiceState.STARTING:
        st.button("Starting…", key=f"{key_prefix}-starting", disabled=True)
        return

    if state == ServiceState.STOPPING:
        st.button("Stopping…", key=f"{key_prefix}-stopping", disabled=True)
        return

    if state == ServiceState.FAILED:
        if st.button("Start", key=f"{key_prefix}-start", help="Service previously failed; see logs."):
            worker.start_service(name)
            st.rerun()
        return

    # STOPPED or UNAVAILABLE.
    if not is_available:
        installable = worker.service(name).primary_installable()
        if installable is not None:
            render_inline_install(installable, key_prefix=f"{key_prefix}-install")
        else:
            st.caption("Not installed")
        return

    if st.button("Start", key=f"{key_prefix}-start"):
        worker.start_service(name)
        st.rerun()


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

    _render_badge(status.state)
    _render_action_button(
        status.state,
        svc.is_available(),
        worker,
        name,
        key_prefix,
    )

    if show_web_ui_link:
        endpoint = getattr(svc, "web_ui_endpoint", lambda: None)()
        if status.state == ServiceState.RUNNING and endpoint:
            st.link_button("Open Web UI", endpoint)
