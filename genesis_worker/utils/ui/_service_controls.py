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


def render_action_button(
    state: ServiceState,
    is_available: bool,
    worker,
    name: str,
    key_prefix: str,
    *,
    use_container_width: bool = False,
) -> None:
    """Render the action button for the current state.

    Public so callers that want to lay the button out themselves (e.g. the
    dashboard's action row beside the Admin button) can do so without having
    to re-implement the state-to-button mapping.

    ``use_container_width`` stretches the button to fill its column. The
    status pages default to ``False`` (single full-width column, content
    width is fine); the dashboard cards pass ``True`` so Start/Stop/Install
    matches the Admin button's width.

    The states map to: active Stop (RUNNING or STARTING — the latter so a
    container stuck in a reboot loop can be killed from the UI), disabled
    Stopping… (STOPPING, polled and auto-refreshed when the container
    reaches STOPPED), inline install (!available and STOPPED/UNAVAILABLE),
    Start (available and STOPPED/FAILED/UNAVAILABLE).
    """
    # RUNNING and STARTING both render an active Stop. STARTING happens
    # when the container is up but the health probe is still failing
    # (see DockerService.status) — including containers in a reboot loop
    # where every restart cycle lands on "running + unhealthy". Folding
    # it into the Stop branch gives the user an escape hatch without
    # having to SSH in to kill the container.
    #
    # The Stop button is wrapped in a 2-second polling fragment. The
    # fragment tracks the last-seen state in session_state and triggers
    # a full app rerun whenever it changes. Without this polling, the
    # page would show "Starting…" indefinitely once a service starts
    # and the Web UI link would never appear even though the container
    # is reachable — the badge and Web UI link live OUTSIDE this
    # fragment, so they only redraw when the full app reruns.
    #
    # Tracking via "last seen" rather than "is current outside the
    # active set" matters because STARTING -> RUNNING stays inside
    # (RUNNING, STARTING) but is still a transition the user needs to
    # see: the badge goes from "Starting…" (orange) to "Running"
    # (green) and the Web UI link appears. The previous "is current
    # outside the active set" check missed this transition and left
    # the badge stuck on "Starting…" forever.
    if state in (ServiceState.RUNNING, ServiceState.STARTING):
        last_state_key = f"_polled_state_{key_prefix}"

        @st.fragment(run_every="2s")
        def _polled_stop_button(*, btn_key: str) -> None:
            current = worker.service_status(name).state
            last = st.session_state.get(last_state_key)
            if last is None:
                # First poll: record the state without rerunning —
                # the outer page just rendered with this state, so
                # there's nothing new to draw.
                st.session_state[last_state_key] = current
            elif current != last:
                # State changed (e.g. STARTING -> RUNNING). Rerun the
                # full app so the badge / Web UI link outside this
                # fragment redraw with the new state.
                st.session_state[last_state_key] = current
                st.rerun(scope="app")
            if st.button("Stop", key=btn_key, use_container_width=use_container_width):
                worker.stop_service(name)
                st.rerun()

        _polled_stop_button.__name__ = f"_polled_stop_button_{key_prefix}"
        _polled_stop_button(btn_key=f"{key_prefix}-stop")
        return

    # Surface any error from the previous Start attempt. The framework returns
    # StartResult(ok=False, message=...) when docker run fails (port collision,
    # image missing, etc.); without this, the user clicks Start and the page
    # silently returns to "Start" with no explanation.
    error_key = f"{key_prefix}-start_error"
    pending_error = st.session_state.pop(error_key, None)
    if pending_error:
        st.error(pending_error)

    if state == ServiceState.STOPPING:
        last_state_key = f"_polled_state_{key_prefix}"

        @st.fragment(run_every="2s")
        def _wait_for_stop(*, btn_key: str) -> None:
            current = worker.service_status(name).state
            last = st.session_state.get(last_state_key)
            if last is None:
                st.session_state[last_state_key] = current
            elif current != last:
                # State changed (STOPPING -> STOPPED, FAILED, etc.).
                # Rerun the full app so the outer badge / button
                # redraw with the new state.
                st.session_state[last_state_key] = current
                st.rerun(scope="app")
            st.button(
                "Stopping…",
                key=btn_key,
                disabled=True,
                use_container_width=use_container_width,
            )

        _wait_for_stop.__name__ = f"_wait_for_{key_prefix}_stopping"
        _wait_for_stop(btn_key=f"{key_prefix}-stopping")
        return

    if state == ServiceState.FAILED:
        if st.button(
            "Start",
            key=f"{key_prefix}-start",
            help="Service previously failed; see logs.",
            use_container_width=use_container_width,
        ):
            result = worker.start_service(name)
            if not result.ok:
                st.session_state[f"{key_prefix}-start_error"] = result.message
            st.rerun()
        return

    # STOPPED or UNAVAILABLE.
    if not is_available:
        installable = worker.service(name).primary_installable()
        if installable is not None:
            render_inline_install(
                installable,
                key_prefix=f"{key_prefix}-install",
                use_container_width=use_container_width,
            )
        else:
            st.caption("Not installed")
        return

    if st.button("Start", key=f"{key_prefix}-start", use_container_width=use_container_width):
        result = worker.start_service(name)
        if not result.ok:
            st.session_state[f"{key_prefix}-start_error"] = result.message
        st.rerun()


def render_service_controls(
    svc: InferenceService,
    status: ServiceStatus,
    *,
    show_web_ui_link: bool = True,
    show_action_button: bool = True,
    key_prefix: str = "",
) -> None:
    """Render service info: state badge, Start/Stop, inline install, Web UI link.

    ``key_prefix`` namespaces Streamlit widget keys to avoid collisions when
    multiple instances appear on the same page.

    ``show_action_button`` and ``show_web_ui_link`` are independent toggles so
    callers that want to lay the action button and the Web UI link out
    themselves (e.g. the dashboard's action row) can disable them here and
    render them in their own layout via :func:`render_action_button` and the
    service's ``web_ui_endpoint``.

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
    if show_action_button:
        render_action_button(
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
