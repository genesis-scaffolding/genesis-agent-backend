"""Framework dashboard — control surface for managing services and viewing host info.

The catalog and acquisition live on the dedicated Catalog page; this
page stays focused on the host, live diagnostics, and services.
"""

from __future__ import annotations

import streamlit as st

from genesis_worker.ui._nav import to_relative as _to_relative

worker = st.session_state["worker"]

st.title("Genesis Worker")


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
    for info in worker.list_services():
        svc = worker.service(info.name)
        status = worker.service_status(info.name)
        caps = info.capabilities
        estimate = svc.resource_estimate()
        vram_gb = estimate.vram_bytes_typical / (1024 ** 3) if estimate.vram_bytes_typical else 0

        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1, 1])
            with cols[0]:
                st.subheader(info.display_name)
                st.caption(svc.__class__.__name__)
            with cols[1]:
                st.write(f"**{status.state.value.upper()}**")
                if status.pid:
                    st.caption(f"pid {status.pid}")
            with cols[2]:
                if vram_gb:
                    st.write(f"~{vram_gb:.0f} GB VRAM")
            with cols[3]:
                if status.state.value == "running" and st.button(
                    "Stop", key=f"stop-{info.name}"
                ):
                    worker.stop_service(info.name)
                    st.rerun()
                elif status.state.value == "stopped" and st.button(
                    "Start", key=f"start-{info.name}"
                ):
                    worker.start_service(info.name)
                    st.rerun()
            with cols[4]:
                pages = svc.ui_pages
                if pages and st.button("Admin", key=f"admin-{info.name}"):
                    st.switch_page(_to_relative(pages[0].path))
                endpoint = svc.web_ui_endpoint()
                if (
                    caps.has_web_ui
                    and status.state.value == "running"
                    and endpoint
                    and st.link_button("Web UI ↗", endpoint, key=f"webui-{info.name}")
                ):
                    pass


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
