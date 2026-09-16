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

    Returns the service name when the URL ends with ``<name>_status``;
    None otherwise (the caller falls back to ``?svc=``).
    """
    url = getattr(getattr(st, "context", None), "url", None)
    if not url:
        return None
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if path.endswith(_STATUS_SUFFIX):
        candidate = path[: -len(_STATUS_SUFFIX)]
        try:
            return worker.service(candidate).name
        except KeyError:
            return None
    return None


def _service_from_query() -> str | None:
    """Resolve a service name from ``?svc=<name>``.

    Falls back to this when the URL isn't available (e.g. test runners).
    """
    params = st.query_params
    raw = params.get("svc") if hasattr(params, "get") else None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return raw if isinstance(raw, str) and raw else None


worker = st.session_state["worker"]
name = _service_from_url(worker) or _service_from_query()
if not name:
    st.error("default_service_status.py: cannot determine service from URL or ?svc=")
    raise SystemExit(1)
svc = worker.service(name)
render_default_status(svc)
