"""Framework dashboard — control surface for managing services and the vault."""

from __future__ import annotations

import time

import streamlit as st

worker = st.session_state["worker"]

st.title("Genesis Worker")

# --- System strip -----------------------------------------------------------
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

st.divider()

# --- Services grid -----------------------------------------------------------
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
                st.switch_page(pages[0].label)
            endpoint = svc.runtime_endpoint()
            if (
                caps.has_web_ui
                and status.state.value == "running"
                and endpoint
                and st.link_button("Web UI ↗", endpoint, key=f"webui-{info.name}")
            ):
                pass

st.divider()

# --- Vault section ------------------------------------------------------------
st.header("Vault")
catalog = worker.catalog()
sources = worker.list_sources()

tab_labels = [s.display_name for s in sources]
tabs = st.tabs(tab_labels) if tab_labels else []

# Rescan button sits above the tabs.
if st.button("↻ Rescan catalog", key="dashboard-rescan"):
    worker.rescan_catalog()
    st.rerun()

if not catalog.entries:
    st.info("Catalog is empty. Acquire a model or check your vault path.")
else:
    # Group entries by source for display.
    by_source: dict[str, list] = {s.name: [] for s in sources}
    for entry in catalog.entries:
        by_source.setdefault(entry.source, []).append(entry)

    for tab, source in zip(tabs, sources, strict=True):
        with tab:
            entries = by_source.get(source.name, [])
            if not entries:
                st.caption("No entries from this source.")
                continue
            for entry in entries:
                with st.expander(entry.name):
                    st.code(str(entry), language="yaml")

# --- Acquire widget ----------------------------------------------------------
st.subheader("Acquire new model")
acquirable = [s for s in sources if s.can_acquire]
if not acquirable:
    st.caption("No sources support acquisition.")
else:
    if len(acquirable) == 1:
        target = acquirable[0]
        if st.button(f"Open {target.display_name} acquire", key="dashboard-acquire-go"):
            st.switch_page(target.ui_pages[0].label)
    else:
        choice = st.selectbox(
            "Source",
            options=acquirable,
            format_func=lambda s: s.display_name,
            key="dashboard-acquire-source",
        )
        if choice and st.button("Go", key="dashboard-acquire-go"):
            st.switch_page(choice.ui_pages[0].label)

# Slow auto-refresh so the dashboard reflects state changes.
time.sleep(10)
st.rerun()