"""Inline install flow — a single Install button plus progress, no version picker.

Used by the dashboard and the service Status pages as the landing UX
when the service is not yet installed. The full per-installable
management surface (version pick, refresh, uninstall) lives on the
Images page.

The shape mirrors the in-flight block in
``genesis_worker/services/llama_swap/ui/binaries.py`` but does not import
from it — duplication is intentional so the dashboard's plumbing stays
free of plugin-specific UI.

An install outcome stays on screen until the operator dismisses it
(ADR-049). It used to be rendered into a run that was immediately
discarded, so a failed pull looked exactly like a no-op.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from genesis_worker.contracts import AcquireSession, AcquireStateKind, AcquireView

_TERMINAL_KINDS = (
    AcquireStateKind.COMPLETE,
    AcquireStateKind.FAILED,
    AcquireStateKind.CANCELLED,
)


def install_session_key(key_prefix: str) -> str:
    """The session_state key of the inline install session for ``key_prefix``."""
    return f"{key_prefix}/session"


def render_inline_install(
    session_factory: Callable[[], AcquireSession | None],
    *,
    key_prefix: str,
    use_container_width: bool = False,
) -> None:
    """Render an Install button; on click, run the install and stream progress.

    ``session_factory`` builds the session the install runs in — the
    caller decides what "install this service" means (ADR-049: the
    service-level install covers every artifact the service needs).

    ``key_prefix`` namespaces the Streamlit widget keys. Two prefixes on
    the same page (e.g. dashboard and a sidebar) is fine — pick anything
    unique.

    ``use_container_width`` stretches the button to fill its column. The
    status pages default to ``False`` (content-width is fine on a single
    full-width column); the dashboard cards pass ``True`` so Install
    matches the Admin button's width.
    """
    sess_key = install_session_key(key_prefix)

    if sess_key in st.session_state:
        render_install_session(
            sess_key=sess_key,
            key_prefix=key_prefix,
            use_container_width=use_container_width,
        )
        return

    if st.button("Install", key=f"{key_prefix}-install", use_container_width=use_container_width):
        session = session_factory()
        if session is None:
            st.error("This service has nothing to install.")
            return
        st.session_state[sess_key] = session
        st.rerun()


def render_install_session(
    *, sess_key: str, key_prefix: str, use_container_width: bool = False
) -> None:
    """Render the live progress — or the outcome — of an install session.

    The terminal outcome is rendered and *stays* on screen until the
    operator clicks Dismiss; only then is the session dropped. The
    session therefore survives every rerun, and so does the message.
    """
    session: AcquireSession = st.session_state[sess_key]
    step = session.view()

    if step.kind in _TERMINAL_KINDS:
        _render_step(step)
        if st.button(
            "Dismiss", key=f"{key_prefix}-dismiss", use_container_width=use_container_width
        ):
            st.session_state.pop(sess_key, None)
            st.rerun()
        return

    @st.fragment(run_every="2s")
    def _progress(session: AcquireSession = session) -> None:
        current = session.view()
        _render_step(current)
        if current.kind in _TERMINAL_KINDS:
            st.rerun(scope="app")

    _progress()

    if st.button("Cancel", key=f"{key_prefix}-cancel", use_container_width=use_container_width):
        session.cancel()
        st.rerun()


def _render_step(step: AcquireView) -> None:
    if step.kind == "complete":
        st.success(step.title or "complete")
    elif step.kind == "failed":
        st.error(f"{step.title or 'failed'} — {step.error or 'unknown error'}")
    elif step.kind == "cancelled":
        st.warning(step.title or "cancelled")
    elif step.kind == "fetching" and step.progress is not None:
        total = step.progress.bytes_total or step.total_bytes or 1
        pct = step.progress.bytes_done / total if total else 0
        st.progress(
            min(pct, 1.0),
            text=f"{step.title or 'fetching'} · {step.progress.bytes_done}/{total}",
        )
    else:
        st.info(step.title or step.kind)
