"""UI panel registry — named renderers invoked by ``render_default_status``.

Four panels ship in v1:

- ``service_info`` -- display name + Start/Stop controls + Web UI link.
- ``container_info`` -- image ref, container name, listen address, web UI URL.
  Docker-only; the YAML loader skips it for uv services.
- ``auth_token`` -- the JWT info / copy-to-clipboard token block that the
  current crawl4ai status page renders. Mirrors the existing UX exactly.
- ``log_tail`` -- ``render_tail_log``.

Adding a new panel is one function in this module + a registered name.
"""

from __future__ import annotations

import html
from collections.abc import Callable

import streamlit as st
import streamlit.components.v1 as components

from ...contracts import InferenceService
from ..ui._service_controls import render_service_controls
from ..ui._tail_log import render_tail_log

PanelRenderer = Callable[[InferenceService, dict], None]

_REGISTRY: dict[str, PanelRenderer] = {}


def register(kind: str) -> Callable[[PanelRenderer], PanelRenderer]:
    """Decorator: register ``fn`` under ``kind`` for ``render()`` to dispatch to."""

    def decorator(fn: PanelRenderer) -> PanelRenderer:
        if kind in _REGISTRY:
            raise ValueError(f"panel kind {kind!r} already registered")
        _REGISTRY[kind] = fn
        return fn

    return decorator


def registered_kinds() -> list[str]:
    """Names of all registered panel kinds. Test/debug aid."""
    return sorted(_REGISTRY)


def render(svc: InferenceService, panels: list[str], panel_config: dict) -> None:
    """Invoke each registered renderer in ``panels``, in order.

    ``panel_config`` is forwarded to every renderer as a dict so each
    can read its own keys. Missing kinds raise -- the YAML loader
    validates the panel names at construction time.
    """
    for kind in panels:
        renderer = _REGISTRY.get(kind)
        if renderer is None:
            raise ValueError(f"unknown panel kind {kind!r}; registered: {sorted(_REGISTRY)}")
        renderer(svc, panel_config)


# --- renderers --------------------------------------------------------------


@register("service_info")
def _service_info(svc: InferenceService, _panel_config: dict) -> None:
    """Display name + Start/Stop controls + Web UI link.

    Reproduces the bordered ``Service info`` block from the existing
    crawl4ai / sillytavern / cptr status pages, sans per-service
    configuration rows (those vary; YAML services that need extras
    override ``ui_pages`` to add their own page).
    """
    worker = st.session_state["worker"]
    with st.container(border=True):
        st.header("Service info")
        status = worker.service_status(svc.name)
        render_service_controls(svc, status, key_prefix=f"status-{svc.name}")


@register("container_info")
def _container_info(svc: InferenceService, _panel_config: dict) -> None:
    """Image ref + container name + listen address + web UI URL (docker only).

    Reads ``image_ref``, ``listen_address``, ``container_name``, and
    ``web_ui_endpoint`` from the service. These are exposed on
    :class:`DockerService` directly; non-docker services skip this
    panel (their YAML ``ui.status.panels`` doesn't list it).
    """
    image_ref = getattr(svc, "image_ref", None)
    container_name = getattr(svc, "container_name", None)
    listen_address = getattr(svc, "listen_address", None)
    web_ui_endpoint = getattr(svc, "web_ui_endpoint", lambda: None)()
    public_host = getattr(svc, "public_host", lambda: "localhost")()

    with st.container(border=True):
        st.subheader("Container info")
        cols = st.columns(2)
        with cols[0]:
            if image_ref is not None:
                st.markdown(f"**Image:** `{image_ref}`")
            if container_name is not None:
                st.markdown(f"**Container name:** `{container_name}`")
            if listen_address is not None:
                st.markdown(f"**Listen:** `{listen_address}`")
        with cols[1]:
            if web_ui_endpoint is not None:
                st.markdown(f"**Web UI:** `{web_ui_endpoint}`")
            elif listen_address is not None:
                # When stopped, web_ui_endpoint() returns None -- show the
                # canonical address anyway so the link is informative.
                port = listen_address.rsplit(":", 1)[-1]
                st.markdown(f"**Public URL:** `http://{public_host}:{port}/`")


@register("auth_token")
def _auth_token(svc: InferenceService, panel_config: dict) -> None:
    """JWT info or copy-to-clipboard token block (mirrors the old crawl4ai page).

    Reads ``svc.auth_token()`` and ``svc.auth_enabled()``. When ``auth_enabled``
    is True, renders the JWT info message. Otherwise, when a token is present,
    renders the bearer header + an HTML/JS copy-to-clipboard control (same UX
    as the old ``services/crawl4ai/ui/status.py``).
    """
    token = getattr(svc, "auth_token", lambda: None)()
    enabled = getattr(svc, "auth_enabled", lambda: False)()
    env_var = panel_config.get("token_env_var", "API_TOKEN")
    source_label = panel_config.get("source_label", "API token")

    with st.container(border=True):
        st.subheader(f"{source_label}")

        if enabled:
            st.info(
                f"Authentication is enabled (`{env_var}_JWT_ENABLED=true`). "
                "Tokens are issued by your external auth provider; this "
                "service doesn't store one."
            )
            return

        if token is None:
            st.warning(
                "No token yet. Start the service once to auto-generate one "
                "and persist it under `<state_dir>`, or set the option in settings."
            )
            return

        fallback_label = panel_config.get("fallback_label")
        options_obj = getattr(svc, "options", None)
        if (
            fallback_label
            and options_obj is not None
            and getattr(options_obj, fallback_label, None)
        ):
            source_note = f"from settings (`{fallback_label}` option)"
        else:
            source_note = (
                "persisted on disk (mode `0o600`; only the host user running "
                "the worker can read it)"
            )

        st.caption(f"Authorization header: `Bearer <token>`  ·  source: {source_note}")
        _render_copy_token(token)


def _render_copy_token(token: str) -> None:
    """One-click copy via the browser's Clipboard API (same UX as crawl4ai)."""
    escaped = html.escape(token)
    components.html(
        f"""
        <div style="display: flex; align-items: stretch; gap: 0.5rem;
                    font-family: monospace;">
            <code id="svc-token"
                  style="flex: 1; padding: 0.5rem 0.75rem; background: #f0f0f0;
                         border-radius: 0.25rem; overflow-x: auto;
                         white-space: nowrap;">{escaped}</code>
            <button id="svc-copy-btn"
                    style="padding: 0.5rem 1rem; border: 1px solid #ccc;
                           border-radius: 0.25rem; background: #fff;
                           cursor: pointer; white-space: nowrap;
                           font-family: inherit;">
                📋 Copy
            </button>
        </div>
        <script>
            const btn = document.getElementById("svc-copy-btn");
            const code = document.getElementById("svc-token");
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


@register("log_tail")
def _log_tail(svc: InferenceService, panel_config: dict) -> None:
    """Auto-refreshing log tail (``render_tail_log``)."""
    n_bytes = int(panel_config.get("n_bytes", 8 * 1024))
    with st.container(border=True):
        st.subheader("Console")
        render_tail_log(svc, n_bytes=n_bytes, key=svc.name)


@register("configure")
def _configure(svc: InferenceService, _panel_config: dict) -> None:
    """Auto-generated form for the service's user-editable options.

    Reads ``svc.config.option_specs`` (the original ``OptionSpec``
    dict the loader preserves from the YAML) and renders one widget
    per option, grouped by ``ui_group``. Apply persists to the
    right file (scalars to ``user-overrides.env``; maps to the
    per-service JSON sidecar) and rebuilds the in-memory service
    so the next ``start()`` picks up the new config (ADR-036).
    """
    from .configure_panel import render_configure_panel

    render_configure_panel(svc)


__all__ = [
    "PanelRenderer",
    "register",
    "registered_kinds",
    "render",
]
