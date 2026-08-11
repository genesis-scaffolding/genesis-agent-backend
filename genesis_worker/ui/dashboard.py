"""Framework dashboard — control surface for managing services and the vault."""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

worker = st.session_state["worker"]

# st.switch_page resolves paths relative to the main app script's directory,
# which for us is this file's directory (genesis_worker/ui/).
_FRAMEWORK_UI = Path(__file__).parent


def _to_relative(page_path: Path) -> str:
    """Return ``page_path`` as a path string relative to the main script's dir.

    ``st.switch_page`` requires a file path relative to the directory of the
    main app script (``genesis_worker/ui/app.py``). ``Path.relative_to``
    refuses ``..`` segments, so we use ``os.path.relpath`` which handles
    sibling directories like ``../services/llama_swap/ui/status.py``.
    """
    import os.path

    return os.path.relpath(str(page_path), start=str(_FRAMEWORK_UI))


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
                st.switch_page(_to_relative(pages[0].path))
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

# Rescan button: disabled while running, spinner during, success message after.
rescanning = st.session_state.get("dashboard_rescanning", False)
if st.button(
    "↻ Rescan catalog",
    key="dashboard-rescan",
    disabled=rescanning,
):
    st.session_state["dashboard_rescanning"] = True
    st.rerun()

if rescanning:
    with st.spinner("Scanning vault…"):
        result = worker.rescan_catalog()
    total = sum(len(v) for v in result.by_source().values())
    st.session_state["dashboard_rescanning"] = False
    st.toast(f"Found {total} entries")
    st.rerun()

catalog_by_source = catalog.by_source()
total = sum(len(v) for v in catalog_by_source.values())
if total == 0:
    st.info("Catalog is empty. Acquire a model or check your vault path.")
else:
    for tab, source in zip(tabs, sources, strict=True):
        with tab:
            entries = catalog_by_source.get(source.name, [])
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
        info = acquirable[0]
        if st.button(f"Open {info.display_name} acquire", key="dashboard-acquire-go"):
            st.switch_page(_to_relative(worker.source(info.name).ui_pages[0].path))
    else:
        choice_info = st.selectbox(
            "Source",
            options=acquirable,
            format_func=lambda s: s.display_name,
            key="dashboard-acquire-source",
        )
        if choice_info and st.button("Go", key="dashboard-acquire-go"):
            st.switch_page(
                _to_relative(worker.source(choice_info.name).ui_pages[0].path)
            )

st.divider()

# --- Debug panel -----------------------------------------------------------
# Shows what the worker has actually resolved: env vars, paths, exists-checks,
# and a raw walker count vs. the catalog count. Useful when the catalog comes
# up empty and you need to know whether it's a path issue or a walker issue.
with st.expander("Debug: paths and catalog walk", expanded=False):
    import os

    paths = worker.settings.paths
    vault = paths.resolved_vault_path

    def _row(label: str, value: object, exists: bool | None = None) -> None:
        suffix = ""
        if exists is True:
            suffix = "  ✓ exists"
        elif exists is False:
            suffix = "  ✗ MISSING"
        st.code(f"{label:<28} {value}{suffix}", language=None)

    st.markdown("**Environment**")
    _row("MODELS_ROOT", os.environ.get("MODELS_ROOT", "<unset>"))
    _row("GENESIS_PATHS__VAULT_PATH", os.environ.get("GENESIS_PATHS__VAULT_PATH", "<unset>"))
    _row("XDG_DATA_HOME", os.environ.get("XDG_DATA_HOME", "<unset>"))
    _row("HOME", os.environ.get("HOME", "<unset>"))

    st.markdown("**Resolved paths**")
    _row("vault_path", vault, vault.exists())

    sources_list = worker.list_sources()
    for info in sources_list:
        src = worker.source(info.name)
        lp = src.local_path
        _row(f"{info.name}.local_path", lp, lp.exists())

    st.markdown("**Raw walker vs catalog**")
    # Count models--* dirs directly under each source's local_path so we can
    # see whether the walker sees the right directories.
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
            # Show first rejection so we can see why.
            for d in raw_dirs[:1]:
                refs = d / "refs" / "main"
                snapshots = d / "snapshots"
                st.caption(
                    f"  {d.name}: refs/main exists={refs.is_file()}, "
                    f"snapshots exists={snapshots.is_dir()}"
                )

# Slow auto-refresh so the dashboard reflects state changes.
time.sleep(10)
st.rerun()