"""Streamlit live console tail — auto-refreshing fragment for a service's log file."""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import InferenceService


def render_tail_log(
    svc: InferenceService,
    *,
    n_bytes: int = 8192,
    key: str = "",
) -> None:
    """Render an auto-refreshing console tail for ``svc``'s log file.

    ``n_bytes`` is the number of bytes to read from the end of the log file.
    ``key`` namespaces the fragment; multiple calls on the same page must
    pass distinct keys to avoid Streamlit fragment collisions.

    The caller is responsible for wrapping in ``st.container(border=True)``
    and adding a subheader.
    """
    # ``key`` is used to derive a unique fragment function name. Streamlit
    # identifies fragments by the underlying function's ``__name__``, so each
    # distinct key produces a distinct fragment.
    frag_name = f"_tail_log_{key}" if key else "_tail_log"

    def _tail_fragment() -> None:
        content = getattr(svc, "tail_log", lambda n: "")(n_bytes)
        if content:
            st.code(content, language=None)
        else:
            st.caption("No log output yet.")

    _tail_fragment.__name__ = frag_name
    st.fragment(run_every="2s")(_tail_fragment)()
