"""Landing page for the llama-swap service."""

from __future__ import annotations

from typing import Any

import streamlit as st

from genesis_worker.contracts.host import GpuDevice
from genesis_worker.utils.ui._nav import to_relative
from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)


def _gpu_choice_label(device: GpuDevice) -> str:
    """Display label for a device in a selectbox."""
    return f"{device.label}  ({device.vendor}, idx {device.index})"


def _coerce_gpu(raw: Any) -> GpuDevice | None:
    """Hydrate an overrides-style dict into a GpuDevice, or return None."""
    if raw is None:
        return None
    if isinstance(raw, GpuDevice):
        return raw
    if isinstance(raw, dict):
        try:
            return GpuDevice(**raw)
        except (TypeError, ValueError):
            return None
    return None


st.title(svc.display_name)

# --- Service info + Configuration ------------------------------------------
with st.container(border=True):
    st.header("Service info")
    render_service_controls(
        svc, worker.service_status(SERVICE_NAME), key_prefix="status-llama_swap"
    )

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
            st.warning(f"No variant matched. Falling back to legacy: `{legacy}`")
        else:
            st.error("No llama-server binary available. Install a variant via the Binaries page.")


# --- Default GPU ----------------------------------------------------------
# Per-machine device pin (ADR-039). When ``default_gpu`` is unset, the
# framework auto-picks deterministically (NVIDIA 0 → AMD 0 → Intel 0);
# when set, that specific device is pinned and the matching variant
# binary is required (fail loud at config-regen time). The dropdown
# shows real devices only — no "use cascade" deflect — and the smart
# pick is the pre-selected default.
def _on_default_gpu_change() -> None:
    raw = st.session_state["status-default-gpu"]
    gpu = _coerce_gpu(raw)
    svc.set_default_gpu(gpu)
    worker.regenerate_service_config(SERVICE_NAME)


with st.container(border=True):
    st.subheader("Default GPU")
    devices = svc.host_info.hardware.devices
    if not devices:
        st.info("No GPUs detected on this host.")
    else:
        gpu_labels = [_gpu_choice_label(d) for d in devices]
        # Smart default = the framework's deterministic pick. Shown as
        # the dropdown's pre-selected value when no explicit default_gpu
        # is persisted.
        current_gpu = svc.effective_default_gpu
        if current_gpu is None:
            current_idx = 0
        else:
            current_label = _gpu_choice_label(current_gpu)
            current_idx = gpu_labels.index(current_label) if current_label in gpu_labels else 0
        choice = st.selectbox(
            "default GPU",
            gpu_labels,
            index=current_idx,
            key="status-default-gpu",
            on_change=_on_default_gpu_change,
        )
        chosen = next(
            (d for d in devices if _gpu_choice_label(d) == choice),
            None,
        )
        if chosen is not None:
            variant = "cuda" if chosen.vendor == "nvidia" else "vulkan"
            st.caption(
                f"Pinned to {chosen.vendor} idx {chosen.index}. "
                f"Requires `llama-server-{variant}` to be installed."
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
