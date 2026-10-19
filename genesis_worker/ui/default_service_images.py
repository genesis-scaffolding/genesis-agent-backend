"""Streamlit dispatcher for declarative service image pages.

Companion to ``default_service_status.py``. Each declarative service's
``UiPage`` for the Images page points at this script with a distinct
``url_path`` (``<service_name>_images``). The script resolves the
service name the same way the status dispatcher does and renders the
per-installable version picker from
:mod:`genesis_worker.utils.ui.default_images`.

One dispatcher (not one page file per YAML service) keeps the install
/ version-picker behaviour in one place: any change propagates to
every declarative docker / compose service at once.
"""

from __future__ import annotations

from urllib.parse import urlparse

import streamlit as st

from genesis_worker.utils.ui.default_images import render_default_images

_IMAGES_SUFFIX = "_images"


def _service_from_url(worker) -> str | None:
    """Resolve a service name from the current Streamlit URL.

    Same strategy as the status dispatcher: URL path ending in
    ``<name>_images``, then the ``page`` query parameter (multi-page
    apps pass the ``url_path`` through it), then ``?svc=<name>``.
    """
    url = getattr(getattr(st, "context", None), "url", None)
    if url:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if path.endswith(_IMAGES_SUFFIX):
            candidate = path[: -len(_IMAGES_SUFFIX)]
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
        if candidate.endswith(_IMAGES_SUFFIX):
            name_guess = candidate[: -len(_IMAGES_SUFFIX)]
            try:
                return worker.service(name_guess).name
            except KeyError:
                pass

    raw = params.get("svc")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, str) and raw:
        return raw
    return None


worker = st.session_state["worker"]
name = _service_from_url(worker)
if not name:
    st.error(
        "default_service_images.py: cannot determine service. "
        f"url={getattr(getattr(st, 'context', None), 'url', None)!r}, "
        f"query_params={dict(st.query_params)!r}"
    )
    st.stop()
svc = worker.service(name)
render_default_images(svc)
