"""Smoke checks for the service controls helper.

The full button-rendering logic runs under Streamlit's runtime and
is hard to unit-test in a bare pytest session. These tests cover what
we can verify without a Streamlit context: the public API, the import
surface, and the state-to-action mapping (via a small introspection
table that mirrors the helper's branching).
"""

from __future__ import annotations

import inspect


def test_render_service_controls_signature_unchanged() -> None:
    """The helper is used by cptr, llama-swap, and comfyui — guard the signature."""
    from genesis_worker.utils.ui._service_controls import render_service_controls

    sig = inspect.signature(render_service_controls)
    params = list(sig.parameters.keys())
    assert params == ["svc", "status", "show_web_ui_link", "show_action_button", "key_prefix"]
    # Defaults preserved.
    assert sig.parameters["show_web_ui_link"].default is True
    assert sig.parameters["show_action_button"].default is True
    assert sig.parameters["key_prefix"].default == ""


def test_render_action_button_signature_unchanged() -> None:
    """The action button helper is called from the dashboard's action row."""
    from genesis_worker.utils.ui._service_controls import render_action_button

    sig = inspect.signature(render_action_button)
    params = list(sig.parameters.keys())
    assert params == [
        "state",
        "is_available",
        "worker",
        "name",
        "key_prefix",
        "use_container_width",
    ]
    assert sig.parameters["use_container_width"].default is False


def test_service_state_branches_cover_every_value() -> None:
    """Every :class:`ServiceState` should have an explicit branch in the helper."""
    from genesis_worker.contracts import ServiceState
    from genesis_worker.utils.ui._service_controls import render_action_button

    # Read the function source as text and grep for each state name.
    src = inspect.getsource(render_action_button)
    for state in ServiceState:
        # All states except the fall-through STOPPED / UNAVAILABLE branch
        # should appear as an explicit ``if`` or ``elif``. The helper may
        # group two states into one branch via ``state in (X, Y)`` —
        # accept either form.
        if state in (ServiceState.STOPPED, ServiceState.UNAVAILABLE):
            continue
        in_eq = f"== ServiceState.{state.name}" in src
        in_tuple = f"ServiceState.{state.name}," in src or f"ServiceState.{state.name})" in src
        assert in_eq or in_tuple, f"no explicit branch for ServiceState.{state.name}"


def test_render_badge_uses_color_coding() -> None:
    """The badge function should differentiate running, starting, failed, etc."""
    from genesis_worker.utils.ui._service_controls import _render_badge

    src = inspect.getsource(_render_badge)
    # Spot-check the colours; if these change, the visual signal changes.
    assert '"green"' in src
    assert '"orange"' in src
    assert '"red"' in src


def test_polling_fragment_detects_starting_to_running_transition() -> None:
    """The polling fragment must rerun the full app on STARTING -> RUNNING.

    The previous "current not in (RUNNING, STARTING)" check missed
    this transition (both states are in the set), leaving the badge
    stuck on "Starting…" and the Web UI link invisible even though
    the container was running. The current implementation tracks the
    last-seen state and reruns whenever it changes.
    """
    from genesis_worker.utils.ui._service_controls import render_action_button

    src = inspect.getsource(render_action_button)
    # The fragment must track last-seen state via session_state.
    assert "last_state_key" in src
    assert "st.session_state.get(last_state_key)" in src
    assert "st.session_state[last_state_key] = current" in src
    # And it must trigger a full app rerun when current != last,
    # NOT only when current is outside the active set.
    assert "current != last" in src
    assert 'st.rerun(scope="app")' in src
    # Specifically: the "is current outside the active set" check
    # is the bug we're guarding against — assert it is NOT used for
    # the "should we rerun?" decision.
    assert "current not in (ServiceState.RUNNING, ServiceState.STARTING)" not in src


def test_running_or_starting_branch_polls_for_state_transition() -> None:
    """RUNNING/STARTING Stop button is wrapped in a polling fragment.

    Without the polling fragment, the page would show "Starting…"
    indefinitely once a service starts — the badge wouldn't update
    when the container transitioned to RUNNING, and the Web UI
    link (which only renders for RUNNING state) would never
    appear until the user manually refreshed the page.

    The fragment must rerun the full app on ANY state change, not
    just transitions out of (RUNNING, STARTING) — the STARTING ->
    RUNNING transition is the most common one and stays inside the
    active set.
    """
    from genesis_worker.utils.ui._service_controls import render_action_button

    src = inspect.getsource(render_action_button)
    # The combined RUNNING + STARTING branch must wrap its Stop button
    # in a ``@st.fragment(run_every=...)``.
    assert "state in (ServiceState.RUNNING, ServiceState.STARTING)" in src
    assert "@st.fragment(run_every=" in src
    # The fragment must track the last-seen state and call
    # ``st.rerun(scope="app")`` whenever it changes — the bug this
    # guards against is the previous "is current outside the active
    # set" check that missed STARTING -> RUNNING.
    assert "last_state_key" in src
    assert 'st.rerun(scope="app")' in src
    # Specifically: it must compare ``current != last`` and rerun,
    # not check for being outside (RUNNING, STARTING).
    assert "current != last" in src
