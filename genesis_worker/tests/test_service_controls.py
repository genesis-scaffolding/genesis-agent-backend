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
    assert params == ["state", "is_available", "worker", "name", "key_prefix"]


def test_service_state_branches_cover_every_value() -> None:
    """Every :class:`ServiceState` should have an explicit branch in the helper."""
    from genesis_worker.contracts import ServiceState
    from genesis_worker.utils.ui._service_controls import render_action_button

    # Read the function source as text and grep for each state name.
    src = inspect.getsource(render_action_button)
    for state in ServiceState:
        # All states except the fall-through STOPPED / UNAVAILABLE branch
        # should appear as an explicit ``if`` or ``elif``.
        if state in (ServiceState.STOPPED, ServiceState.UNAVAILABLE):
            continue
        assert f"== ServiceState.{state.name}" in src, (
            f"no explicit branch for ServiceState.{state.name}"
        )


def test_render_badge_uses_color_coding() -> None:
    """The badge function should differentiate running, starting, failed, etc."""
    from genesis_worker.utils.ui._service_controls import _render_badge

    src = inspect.getsource(_render_badge)
    # Spot-check the colours; if these change, the visual signal changes.
    assert '"green"' in src
    assert '"orange"' in src
    assert '"red"' in src
