"""Landing page for the Crawl4AI service."""

from __future__ import annotations

import html

import streamlit as st
import streamlit.components.v1 as components

from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "crawl4ai"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")

    # Status is fetched once per page render. We don't wrap this in a
    # fragment: ``render_service_controls`` may render the inline install
    # flow, which creates its own polling fragment. Nested fragments
    # confuse Streamlit's placeholder reservation during long docker
    # pulls. The Start/Stop button has its own internal polling fragment
    # for the STARTING/STOPPING transitions (see ``render_action_button``).
    render_service_controls(svc, worker.service_status(SERVICE_NAME), key_prefix="status-crawl4ai")

    st.divider()

    st.subheader("Container info")
    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**Image:** `{svc.image_ref}`")
        st.markdown(f"**Container name:** `{svc._options.container_name}`")
        st.markdown(f"**Listen:** `{svc.listen_address}`")
    with cols[1]:
        # ``web_ui_endpoint`` returns None when the container is stopped;
        # fall back to the canonical playground URL so the link is always
        # useful (the dashboard at ``/playground/`` is auth-free).
        ui_url = svc.web_ui_endpoint() or (
            f"http://{svc.public_host()}:{svc._options.listen_port}/playground/"
        )
        st.markdown(f"**Web UI:** `{ui_url}`")

# --- API access -----------------------------------------------------------
with st.container(border=True):
    st.subheader("API access")

    if svc._options.jwt_enabled:
        st.info(
            "JWT authentication is enabled (`CRAWL4AI_JWT_ENABLED=true`). "
            "Tokens are issued by your external auth provider; this service "
            "doesn't store one."
        )
    elif (token := svc.api_token()) is not None:
        if svc._options.api_token:
            source_note = "from settings (`api_token` option)"
        else:
            source_note = (
                f"persisted at `{svc._api_token_path}` (mode `0o600`, only the "
                f"host user running the worker can read it)"
            )
        st.caption(f"Authorization header: `Bearer <token>`  ·  source: {source_note}")
        # Real one-click copy via the browser's Clipboard API. ``st.code``'s
        # built-in copy button requires hover; this gives a discoverable
        # always-visible button. The token is HTML-escaped before being
        # embedded, so user-supplied tokens can't inject markup.
        escaped = html.escape(token)
        components.html(
            f"""
            <div style="display: flex; align-items: stretch; gap: 0.5rem; font-family: monospace;">
                <code id="crawl4ai-token"
                      style="flex: 1; padding: 0.5rem 0.75rem; background: #f0f0f0;
                             border-radius: 0.25rem; overflow-x: auto; white-space: nowrap;">{escaped}</code>
                <button id="crawl4ai-copy-btn"
                        style="padding: 0.5rem 1rem; border: 1px solid #ccc;
                               border-radius: 0.25rem; background: #fff;
                               cursor: pointer; white-space: nowrap; font-family: inherit;">
                    📋 Copy
                </button>
            </div>
            <script>
                const btn = document.getElementById("crawl4ai-copy-btn");
                const code = document.getElementById("crawl4ai-token");
                btn.addEventListener("click", async () => {{
                    try {{
                        await navigator.clipboard.writeText(code.textContent);
                        btn.textContent = "✓ Copied";
                        btn.disabled = true;
                        setTimeout(() => {{
                            btn.textContent = "📋 Copy";
                            btn.disabled = false;
                        }}, 1500);
                    }} catch (e) {{
                        btn.textContent = "✗ Copy failed";
                    }}
                }});
            </script>
            """,
            height=80,
        )
    else:
        st.warning(
            "No API token yet. Start the service once to auto-generate one "
            "and persist it under `<state_dir>/crawl4ai/api_token`, or set "
            "`api_token` in settings."
        )

# --- Console ---------------------------------------------------------------
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, n_bytes=8 * 1024, key="crawl4ai")
