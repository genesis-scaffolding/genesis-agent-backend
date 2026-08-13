"""Inline install flow — a single Install button plus progress, no version picker.

Used by the dashboard and the llama-swap Status page as the landing UX
when the service is not yet installed. The full per-installable
management surface (version pick, refresh, uninstall) lives on the
Binaries page.

The shape mirrors the in-flight block in
``genesis_worker/services/llama_swap/ui/binaries.py`` but does not import
from it — duplication is intentional so the dashboard's plumbing stays
free of plugin-specific UI.

This module is intentionally typed against ``Any`` rather than the
contract types: ``genesis_worker.utils`` is a leaf package (boundary
test), so it cannot import from ``genesis_worker.contracts``. The
plugin/dashboard passes a duck-typed installable and session; the
attributes the helper reads (``install``, ``current_step``, ``cancel``,
``step.kind``, ``step.title``, ``step.progress``, ...) are part of the
public contract surface and exercise against it in the dashboard tests.
"""

from __future__ import annotations

from typing import Any

import streamlit as st


def render_inline_install(installable: Any, *, key_prefix: str) -> None:
    """Render an Install button; on click, run the install and stream progress.

    ``key_prefix`` namespaces the Streamlit widget keys. Two prefixes on
    the same page (e.g. dashboard and a sidebar) is fine — pick anything
    unique.
    """
    sess_key = f"{key_prefix}/session"
    drop_key = f"{key_prefix}/drop_pending"

    if sess_key in st.session_state:
        _render_inflight(sess_key=sess_key, drop_key=drop_key, key_prefix=key_prefix)
    else:
        if st.button("Install", key=f"{key_prefix}-install"):
            st.session_state[sess_key] = installable.install()
            st.rerun()


def _render_inflight(*, sess_key: str, drop_key: str, key_prefix: str) -> None:
    session = st.session_state[sess_key]
    step = session.current_step()

    if step.kind in ("complete", "failed", "cancelled"):
        _render_step(step)
        if st.session_state.get(drop_key):
            st.session_state.pop(sess_key, None)
            st.session_state.pop(drop_key, None)
            st.rerun()
        else:
            st.session_state[drop_key] = True
            if st.button("Dismiss", key=f"{key_prefix}-dismiss"):
                st.session_state.pop(sess_key, None)
                st.session_state.pop(drop_key, None)
                st.rerun()
        return

    _render_step(step)
    render_target = st.empty()

    @st.fragment(run_every="2s")
    def _progress(
        session: Any = session,
        render_target: Any = render_target,
        drop_key: str = drop_key,
        sess_key: str = sess_key,
        key_prefix: str = key_prefix,
    ) -> None:
        current = session.current_step()
        with render_target.container():
            _render_step(current)
        if current.kind in ("complete", "failed", "cancelled") and not st.session_state.get(
            drop_key
        ):
            st.session_state[drop_key] = True
            st.rerun(scope="app")

    _progress()

    if st.button("Cancel", key=f"{key_prefix}-cancel"):
        session.cancel()
        st.rerun()


def _render_step(step: Any) -> None:
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
