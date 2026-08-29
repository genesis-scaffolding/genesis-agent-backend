"""Framework dashboard — control surface for managing services and viewing host info.

The catalog and acquisition live on the dedicated Catalog page; this
page stays focused on the host, live diagnostics, and services.
"""

from __future__ import annotations

import streamlit as st

from genesis_worker.utils.ui._nav import to_relative as _to_relative
from genesis_worker.utils.ui._service_controls import (
    render_action_button,
    render_service_controls,
)

worker = st.session_state["worker"]

st.title("Genesis Worker")

# Streamlit's ``st.columns`` lays out equal-width columns but does not
# equalize their heights — the row's tallest column determines the row
# height, shorter columns top-align and leave empty space below. We use
# a wrapping loop (PER_ROW cards per row) so the layout scales to any
# number of services, and this CSS nudge so cards within the same row
# stretch to the row's tallest member. Both rules are required: the
# first equalizes the column slots, the second stretches each card's
# inner block to fill its column.
st.markdown(
    """<style>
.card-grid [data-testid="stHorizontalBlock"] {
    align-items: stretch;
}
.card-grid [data-testid="stColumn"] > div {
    height: 100%;
}
</style>""",
    unsafe_allow_html=True,
)


# --- Section 1: Host ---------------------------------------------------------
# Auto-refreshing system strip is a fragment so its 10s tick doesn't
# rerender the static host info and paths below. Static parts only
# recompute on user action (button click, page navigation).
with st.container(border=True):
    st.header("Host")

    @st.fragment(run_every="10s")
    def _system_strip() -> None:
        metrics = worker.collect_metrics()
        cols = st.columns(4)
        cols[0].metric("CPU", f"{metrics.cpu_percent:.0f}%")
        cols[1].metric("RAM", f"{metrics.ram_used_gb:.1f} / {metrics.ram_total_gb:.1f} GB")
        if metrics.gpu_percent is not None:
            cols[2].metric("GPU", f"{metrics.gpu_percent:.0f}%")
        else:
            cols[2].metric("GPU", "n/a")
        if metrics.vram_total_gb:
            cols[3].metric("VRAM", f"{metrics.vram_used_gb:.1f} / {metrics.vram_total_gb:.1f} GB")
        else:
            cols[3].metric("VRAM", "n/a")

    _system_strip()

    def _format_uptime(s: int | None) -> str:
        if s is None:
            return "unknown"
        days, rem = divmod(s, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        parts: list[str] = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or not parts:
            parts.append(f"{minutes}m")
        return " ".join(parts)

    st.subheader("About")
    info = worker.collect_host_info()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Hostname:** `{info.hostname}`")
        st.markdown(f"**OS:** {info.os}")
        st.markdown(f"**Arch:** {info.arch}")
        st.markdown(f"**Python:** {info.python}")
    with c2:
        st.markdown(f"**Uptime:** {_format_uptime(info.uptime_s)}")
        if info.public_ip:
            st.markdown(f"**Public IP:** `{info.public_ip}`")
        if info.tailscale_ip:
            st.markdown(f"**Tailscale IP:** `{info.tailscale_ip}`")

    st.subheader("Paths")
    paths = worker.settings.paths
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Vault:** `{paths.resolved_vault_path}`")
        st.markdown(f"**Data:** `{paths.data_dir}`")
        st.markdown(f"**Config:** `{paths.config_dir}`")
    with c2:
        st.markdown(f"**Cache:** `{paths.cache_dir}`")
        st.markdown(f"**State:** `{paths.state_dir}`")
        st.markdown(f"**Repo root:** `{paths.resolved_repo_root}`")


# --- Section 2: Services -----------------------------------------------------
with st.container(border=True):
    st.header("Services")

    services = worker.list_services()
    if not services:
        st.info("No services registered.")
    else:
        PER_ROW = 3
        st.markdown('<div class="card-grid">', unsafe_allow_html=True)
        for row_start in range(0, len(services), PER_ROW):
            row = services[row_start : row_start + PER_ROW]
            cols = st.columns(PER_ROW, gap="medium")
            for col, info in zip(cols, row, strict=False):
                with col:
                    svc = worker.service(info.name)
                    status = worker.service_status(info.name)
                    caps = info.capabilities

                    with st.container(border=True):
                        st.subheader(info.display_name)
                        # Badge only — the action button and Web UI link are
                        # rendered in our own layout below so the action row
                        # can sit beside the Admin button, and the URL is
                        # visible (not a button) under the badge.
                        render_service_controls(
                            svc,
                            status,
                            show_action_button=False,
                            show_web_ui_link=False,
                            key_prefix=f"dash-{info.name}",
                        )

                        # Web UI as a clickable URL under the badge. When
                        # the service isn't running we render a single-line
                        # placeholder so the card layout doesn't shift
                        # between states. The trailing ↗ signals "opens
                        # externally" the way most UIs do.
                        endpoint = getattr(svc, "web_ui_endpoint", lambda: None)()
                        if endpoint and status.state.value == "running":
                            st.markdown(f"[{endpoint} ↗]({endpoint})")
                        else:
                            st.markdown("&nbsp;", unsafe_allow_html=True)

                        st.divider()

                        action_cols = st.columns(2)
                        with action_cols[0]:
                            render_action_button(
                                status.state,
                                svc.is_available(),
                                worker,
                                info.name,
                                key_prefix=f"dash-{info.name}",
                                use_container_width=True,
                            )
                        with action_cols[1]:
                            pages = svc.ui_pages
                            if pages and st.button(
                                "Admin",
                                key=f"admin-{info.name}",
                                use_container_width=True,
                            ):
                                st.switch_page(_to_relative(pages[0].path))
        st.markdown("</div>", unsafe_allow_html=True)


# --- Section 3: Sources ------------------------------------------------------
with st.container(border=True):
    st.header("Sources")

    sources = worker.list_sources()
    if not sources:
        st.info("No sources registered.")
    else:
        PER_ROW = 3
        st.markdown('<div class="card-grid">', unsafe_allow_html=True)
        for row_start in range(0, len(sources), PER_ROW):
            row = sources[row_start : row_start + PER_ROW]
            cols = st.columns(PER_ROW, gap="medium")
            for col, info in zip(cols, row, strict=False):
                with col:
                    src = worker.source(info.name)

                    with st.container(border=True):
                        st.subheader(info.display_name)
                        # Source availability — analogous to a service's
                        # running state. ``info.is_available`` is the
                        # source-side check; the framework already called
                        # ``src.is_available()`` when building the list.
                        st.badge(
                            "Available" if info.is_available else "Unavailable",
                            color="green" if info.is_available else "gray",
                        )

                        # Local path is informational only — unlike the
                        # service's URL, there is no external target to
                        # open, so we render plain text.
                        st.caption(str(src.local_path))

                        st.divider()

                        # Sources have no lifecycle (no Start/Stop), so
                        # the action row is a single button that opens
                        # the source's first management page. When the
                        # source has no UI page we render a single-line
                        # placeholder so the card layout doesn't shift.
                        pages = src.ui_pages
                        if pages:
                            if st.button(
                                pages[0].label,
                                key=f"src-open-{info.name}",
                                use_container_width=True,
                            ):
                                st.switch_page(_to_relative(pages[0].path))
                        else:
                            st.markdown("&nbsp;", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


# --- Debug panel -----------------------------------------------------------
# Shows what the worker has actually resolved: env vars, paths, exists-checks,
# and a raw walker count vs. the catalog count. Useful when the catalog comes
# up empty and you need to know whether it's a path issue or a walker issue.
with st.expander("Debug: paths and catalog walk", expanded=False):
    import os

    from dotenv import dotenv_values

    paths = worker.settings.paths
    vault = paths.resolved_vault_path
    catalog = worker.catalog()

    # pydantic-settings reads .env via dotenv but does not populate os.environ.
    # A value in .env is therefore invisible to naive os.environ.get().
    # Read .env ourselves so the panel reflects what the framework sees.
    try:
        env_file_values = dotenv_values(".env")
    except Exception:  # noqa: BLE001 — no .env or unreadable; panel degrades
        env_file_values = {}

    def _shown(name: str) -> str:
        if name in os.environ:
            return os.environ[name]
        if name in env_file_values:
            return f"{env_file_values[name]}  (from .env)"
        return "<unset>"

    def _row(label: str, value: object, exists: bool | None = None) -> None:
        suffix = ""
        if exists is True:
            suffix = "  ✓ exists"
        elif exists is False:
            suffix = "  ✗ MISSING"
        st.code(f"{label:<28} {value}{suffix}", language=None)

    st.markdown("**Environment**")
    _row("MODELS_ROOT", _shown("MODELS_ROOT"))
    _row("GENESIS_PATHS__VAULT_PATH", _shown("GENESIS_PATHS__VAULT_PATH"))
    _row("XDG_DATA_HOME", _shown("XDG_DATA_HOME"))
    _row("HOME", _shown("HOME"))

    st.markdown("**Resolved paths**")
    _row("vault_path", vault, vault.exists())

    sources_list = worker.list_sources()
    for info in sources_list:
        src = worker.source(info.name)
        lp = src.local_path
        _row(f"{info.name}.local_path", lp, lp.exists())

    st.markdown("**Service config paths**")
    for info in worker.list_services():
        svc = worker.service(info.name)
        try:
            cp = svc.config_path
        except Exception as exc:  # noqa: BLE001 — diagnostic panel
            st.write(f"{info.name}: config_path unavailable ({exc})")
            continue
        st.write(f"{info.name}: `{cp}` (exists: {cp.exists()})")
        if cp.exists():
            import os as _os

            st.caption(f"  mtime: {_os.path.getmtime(cp):.0f}, size: {cp.stat().st_size} bytes")

    st.markdown("**Raw walker vs catalog**")
    for info in sources_list:
        src = worker.source(info.name)
        lp = src.local_path
        if not lp.exists():
            st.write(f"{info.name}: path missing — nothing to walk")
            continue
        raw_dirs = [p for p in sorted(lp.iterdir()) if p.is_dir() and p.name.startswith("models--")]
        catalog_entries = catalog.by_source().get(info.name, [])
        st.write(
            f"{info.name}: raw `models--*` dirs = **{len(raw_dirs)}**, "
            f"catalog entries = **{len(catalog_entries)}**"
        )
        if raw_dirs and not catalog_entries:
            st.caption(
                "Walker found directories but the catalog is empty — "
                "the walker's validation is rejecting them."
            )
            for d in raw_dirs[:1]:
                refs = d / "refs" / "main"
                snapshots = d / "snapshots"
                st.caption(
                    f"  {d.name}: refs/main exists={refs.is_file()}, "
                    f"snapshots exists={snapshots.is_dir()}"
                )
