"""Helpers for navigation between framework pages."""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import UiPage


def switch_to(page: UiPage) -> None:
    """Navigate to a page by its URL pathname.

    ``st.switch_page`` given a file path matches the registered pages by
    path and takes the first match. The framework's shared status and image
    dispatchers are one file serving many services, so a file path does not
    identify a page. Passing the ``st.Page`` routes by URL pathname, which
    is unique per page.
    """
    st.switch_page(
        st.Page(
            str(page.path),
            title=page.label,
            icon=page.icon,
            url_path=page.url_path,
        )
    )
