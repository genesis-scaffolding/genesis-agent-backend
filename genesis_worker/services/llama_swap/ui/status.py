"""Landing page for the llama-swap service."""

from __future__ import annotations

from typing import Any

import streamlit as st

from genesis_worker.contracts.host import ComputeDevice, CpuDevice, GpuDevice
from genesis_worker.utils.ui._nav import to_relative
from genesis_worker.utils.ui._service_controls import render_service_controls
from genesis_worker.utils.ui._tail_log import render_tail_log

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)


def _device_choice_label(device: ComputeDevice) -> str:
    """Display label for a compute device in a selectbox."""
    if isinstance(device, CpuDevice):
        return "CPU (no GPU)"
    if isinstance(device, GpuDevice):
        return f"{device.label}  ({device.vendor}, idx {device.index})"
    raise TypeError(f"unknown device type: {type(device)}")


def _coerce_device(raw: Any) -> ComputeDevice | None:
    """Hydrate an overrides-style dict into a ComputeDevice, or return None.

    Discriminator: presence of ``vendor`` → GpuDevice; otherwise
    CpuDevice (the empty-dict case from yaml roundtrip).
    """
    if raw is None:
        return None
    if isinstance(raw, ComputeDevice):
        return raw
    if isinstance(raw, dict):
        if "vendor" in raw:
            try:
                return GpuDevice(**raw)
            except (TypeError, ValueError):
                return None
        # No vendor → CPU.
        return CpuDevice()
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


# --- Default device --------------------------------------------------------
# Per-machine device pin (ADR-039). When ``default_device`` is unset,
# the framework auto-picks deterministically (NVIDIA 0 → AMD 0 → Intel 0
# → CPU); when set, that specific device is pinned and the matching
# variant binary is required (fail loud at config-regen time).
#
# CPU is *always* a selectable entry in the dropdown, synthesised from
# ``CpuDevice()``. Hosts with no GPUs get a single-entry dropdown;
# hosts with GPUs see the GPUs *and* CPU — picking CPU is an explicit
# override of the GPU smart pick. CPU isn't a "detected device" so it
# doesn't live in ``Hardware.devices``; the dropdown injects it here.
def _dropdown_devices(devices: tuple) -> tuple:
    return (*devices, CpuDevice())


def _on_default_device_change() -> None:
    raw = st.session_state["status-default-device"]
    device = _coerce_device(raw)
    svc.set_default_device(device)
    worker.regenerate_service_config(SERVICE_NAME)


with st.container(border=True):
    st.subheader("Default device")
    gpus = svc.host_info.hardware.devices
    choices = _dropdown_devices(gpus)
    device_labels = [_device_choice_label(d) for d in choices]
    current_device = svc.effective_default_device
    current_label = _device_choice_label(current_device)
    current_idx = device_labels.index(current_label) if current_label in device_labels else 0
    choice = st.selectbox(
        "default device",
        device_labels,
        index=current_idx,
        key="status-default-device",
        on_change=_on_default_device_change,
    )
    chosen = next(
        (d for d in choices if _device_choice_label(d) == choice),
        None,
    )
    if chosen is not None:
        if isinstance(chosen, CpuDevice):
            st.caption("Pinned to CPU. Requires `llama-server-cpu` to be installed.")
        else:
            st.caption(
                f"Pinned to {chosen.vendor} idx {chosen.index}. "
                f"Requires `llama-server-{chosen.variant}` to be installed."
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
