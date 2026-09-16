"""Pure renderer for the default service status page.

The companion Streamlit script lives at
``genesis_worker/ui/default_service_status.py`` and is the
``UiPage.path`` for declarative services. That script handles the
URL -> service lookup; this module handles the per-service panel
rendering.

Keeping this as a pure module (no Streamlit script body at module
level) means it can be imported by tests without a streamlit
context, and pytest can drive :func:`render_default_status`
directly with mocked ``st.*`` calls.
"""

from __future__ import annotations

from ...contracts import InferenceService
from . import panels as _panels


def render_default_status(svc: InferenceService) -> None:
    """Render the default status page for ``svc``.

    Reads ``svc.ui_panels`` (a tuple of panel kind names) and
    dispatches each to its registered renderer via :func:`panels.render`.
    The default panel set is service-kind-specific: docker services
    render ``(service_info, container_info, log_tail)``; uv services
    render ``(service_info, log_tail)``.

    The renderer is looked up via ``_panels.render`` so tests can
    monkeypatch ``panels.render`` and have the substitution take
    effect (avoids the early-binding trap of ``from .panels import
    render``).
    """
    panel_names: list[str] = list(getattr(svc, "ui_panels", ("service_info", "log_tail")))
    panel_config: dict = {}

    import streamlit as st

    st.title(svc.display_name)
    _panels.render(svc, panel_names, panel_config)


__all__ = ["render_default_status"]
