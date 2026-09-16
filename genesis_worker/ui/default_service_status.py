"""Streamlit dispatcher for declarative service status pages.

Each declarative service's ``UiPage`` points at this script with a
distinct ``url_path`` (``<service_name>_status``). Streamlit routes
the URL to this script; the script derives the service name from
the URL and calls :func:`render_default_status` to render the
per-service panel set.

A single dispatcher (rather than one page file per YAML service)
keeps the layout in one place: any change to the default status
shape propagates to every YAML service at once. Python services
that need bespoke layouts override :attr:`ui_pages` to point at
their own scripts under ``services/<name>/ui/``.
"""

from __future__ import annotations

from urllib.parse import urlparse

import streamlit as st

from genesis_worker.utils.services.default_status import render_default_status

_STATUS_SUFFIX = "_status"


def _service_from_url(worker) -> str | None:
    """Resolve a service name from the current Streamlit URL.

    Tries in order:
    1. ``st.context.url`` path ending with ``<name>_status``.
    2. ``st.query_params["page"]`` (Streamlit >= 1.30 passes the
       ``url_path`` as a ``page`` query parameter on multi-page
       apps with explicit ``url_path``).
    3. ``st.query_params["svc"]`` (explicit override).
    """
    url = getattr(getattr(st, "context", None), "url", None)
    if url:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if path.endswith(_STATUS_SUFFIX):
            candidate = path[: -len(_STATUS_SUFFIX)]
            try:
                return worker.service(candidate).name
            except KeyError:
                pass

    params = st.query_params
    page_param = params.get("page")
    candidates: list[str] = []
    if isinstance(page_param, str):
        candidates.append(page_param)
    elif isinstance(page_param, list) and page_param:
        candidates.append(page_param[0])
    for candidate in candidates:
        if candidate.endswith(_STATUS_SUFFIX):
            name_guess = candidate[: -len(_STATUS_SUFFIX)]
            try:
                return worker.service(name_guess).name
            except KeyError:
                pass
    return None


def _service_from_query() -> str | None:
    """Resolve a service name from ``?svc=<name>`` (explicit override)."""
    params = st.query_params
    raw = params.get("svc")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return raw if isinstance(raw, str) and raw else None


worker = st.session_state["worker"]
name = _service_from_url(worker) or _service_from_query()
if not name:
    # Show a diagnostic that explains why nothing rendered. The previous
    # behaviour (SystemExit) was silent from the user's perspective
    # — they saw an empty page and assumed the dispatcher was broken.
    st.error(
        "default_service_status.py: cannot determine service. "
        f"url={getattr(getattr(st, 'context', None), 'url', None)!r}, "
        f"query_params={dict(st.query_params)!r}"
    )
    st.stop()
svc = worker.service(name)
render_default_status(svc)
