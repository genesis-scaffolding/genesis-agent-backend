"""Landing page for the llama-swap service."""

from __future__ import annotations

import streamlit as st

from genesis_worker.utils.ui._nav import to_relative
from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")
    render_service_controls(svc, worker.service_status(SERVICE_NAME), key_prefix="status-llama_swap")

    st.divider()

    st.subheader("Configuration")
    config_path = svc.config_path
    st.markdown(f"`{config_path}`")
    last_gen = svc.last_generated_at()

    if config_path.exists():
        if last_gen:
            st.write(f"✓ generated {last_gen}")
        else:
            st.write("✓ present")
    else:
        st.warning("⚠ not generated — auto-generation will read the catalog and recipes")

    cols = st.columns(2)
    with cols[0]:
        ready = svc.is_ready_to_serve()
        if not ready:
            st.warning(
                "No llama-server binary available. Install a variant via the "
                "Binaries page or set the legacy fallback to a valid path."
            )
        if st.button("↻ Regenerate config", key="status-regen", disabled=not ready):
            ok = worker.regenerate_service_config(SERVICE_NAME)
            if ok:
                st.success("regenerated")
            else:
                st.info("already up to date")
            st.rerun()

    with cols[1]:
        config_editor = next(p for p in svc.ui_pages if p.label == "Config editor")
        if st.button("Manage config →", key="status-manage"):
            st.switch_page(to_relative(config_editor.path))


# --- Variant ---------------------------------------------------------------
# Per-machine setting: which framework-managed llama-server binary the
# config generator uses as the default. ``(legacy)`` keeps the existing
# ``default_binary_rel`` fallback; ``auto`` runs ``nvidia-smi`` and picks
# cuda → vulkan → cpu. The change takes effect immediately; the next
# config regen uses the new resolution.
def _on_variant_change() -> None:
    new = st.session_state["status-variant"]
    svc.set_llama_server_variant(None if new == "(legacy)" else new)

with st.container(border=True):
    st.subheader("Variant")
    option_labels = ["(legacy)", "auto", "cuda", "cpu", "vulkan"]
    current = svc.llama_server_variant or "(legacy)"
    choice = st.selectbox(
        "llama-server variant",
        option_labels,
        index=option_labels.index(current),
        key="status-variant",
        on_change=_on_variant_change,
    )
    resolved = svc.effective_llama_server_binary()
    if resolved is not None:
        st.success(f"Resolved: `{resolved}`")
    else:
        legacy = svc._options.default_binary_rel
        if legacy:
            st.warning(
                f"No variant matched. Falling back to legacy: `{legacy}`"
            )
        else:
            st.error(
                "No llama-server binary available. Install a variant via the "
                "Binaries page."
            )


# --- Binaries --------------------------------------------------------------

with st.container(border=True):
    st.subheader("Binaries")

    for installable in svc.installs():
        version = installable.installed_version() or "—"
        state = "installed" if installable.binary_path() else "not installed"
        cols = st.columns([3, 2, 1])
        with cols[0]:
            st.markdown(f"**{installable.name}**")
        with cols[1]:
            st.write(f"{state} · {version}")
        with cols[2]:
            binaries_page = next(p for p in svc.ui_pages if p.label == "Binaries")
            if st.button("Manage →", key=f"status-binaries-{installable.name}"):
                st.switch_page(to_relative(binaries_page.path))


# --- Console ---------------------------------------------------------------
with st.container(border=True):
    st.subheader("Console")
    render_tail_log(svc, n_bytes=8 * 1024, key="llama_swap")
