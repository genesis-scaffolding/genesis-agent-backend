"""Image management — install / uninstall / version picker for the ComfyUI Docker image.

Mirrors ``genesis_worker/services/llama_swap/ui/binaries.py`` one-for-one,
since the installable has versions and the dashboard needs an
install/uninstall UI. Inline install progress is shown during ``docker
pull`` via a refresh-on-fragment widget.
"""

from __future__ import annotations

import streamlit as st

from genesis_worker.contracts import AcquireStateKind, AcquireView

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title("Image")

st.caption(
    "One installable per upstream image. Active selection lives in "
    "``<state_dir>/comfyui-cuda/current``; pinning is via the dropdown below."
)

_SESSION_KEY_PREFIX = "image/sessions"
_VERSIONS_KEY_PREFIX = "image/available_versions"
_DROP_PENDING_PREFIX = "image/drop_pending"


def _session_key(name: str) -> str:
    return f"{_SESSION_KEY_PREFIX}/{name}"


def _versions_key(name: str) -> str:
    return f"{_VERSIONS_KEY_PREFIX}/{name}"


def _drop_pending_key(name: str) -> str:
    return f"{_DROP_PENDING_PREFIX}/{name}"


def _render_step(step: AcquireView) -> None:
    if step.kind == "complete":
        st.success(step.title or "complete")
    elif step.kind == "failed":
        st.error(f"{step.title or 'failed'} — {step.error or 'unknown error'}")
    elif step.kind == "cancelled":
        st.warning(step.title or "cancelled")
    elif step.kind == "fetching" and step.progress is not None:
        total = step.progress.bytes_total or step.total_bytes or 1
        pct = step.progress.bytes_done / total if total else 0
        st.progress(min(pct, 1.0), text=f"{step.title or 'fetching'} · {step.progress.bytes_done}/{total}")
    else:
        st.info(step.title or step.kind)


for installable in svc.installs():
    name = installable.name
    sess_key = _session_key(name)
    drop_key = _drop_pending_key(name)

    with st.expander(name, expanded=False):
        current = installable.installed_version() or "—"
        binary = installable.binary_path()
        image_present = svc.is_available()
        in_flight = sess_key in st.session_state

        state_label = "installed" if image_present else "not installed"
        st.write(f"State: **{state_label}** — resolved: **{current}**")
        if source_url := installable.source_url():
            st.caption(f"Source: [{source_url}]({source_url})")
        if svc.is_running():
            st.caption("Service is currently running — stop it from the Status page before uninstalling.")

        # Disable install when GPU is required but missing.
        install_disabled_by_gpu = svc._options.gpu_required and not svc.has_nvidia_gpu
        if install_disabled_by_gpu:
            st.caption(
                "Install disabled: NVIDIA GPU required but not detected. "
                "Set `gpu_required: false` in service options to override."
            )

        versions = installable.available_versions()
        st.session_state[_versions_key(name)] = versions

        if not versions:
            st.caption("No tags returned from the registry (rate-limited or unreachable).")
        else:
            labels = [v.version for v in versions]
            try:
                idx = labels.index(current) if current and current != "—" else 0
            except ValueError:
                idx = 0
            choice = st.selectbox(
                "Tag",
                labels,
                index=idx,
                key=f"version-{name}",
            )
            obj = next(v for v in versions if v.version == choice)
            st.caption(f"Image ref: `{obj.url}`")

            cols = st.columns(3)
            with cols[0]:
                install_disabled = in_flight or install_disabled_by_gpu
                install_help = (
                    "An install is already in progress." if in_flight
                    else "GPU required but not detected." if install_disabled_by_gpu
                    else None
                )
                if st.button(
                    "Install" if not image_present else "Reinstall",
                    key=f"install-{name}",
                    disabled=install_disabled,
                    help=install_help,
                ):
                    st.session_state[sess_key] = installable.install(version=choice)
                    st.rerun()
            with cols[1]:
                uninstall_disabled = svc.is_running() or current == "—"
                uninstall_help = (
                    "Stop the service first."
                    if svc.is_running()
                    else "Nothing installed."
                    if current == "—"
                    else None
                )
                if st.button(
                    "Uninstall",
                    key=f"uninstall-{name}",
                    disabled=uninstall_disabled,
                    help=uninstall_help,
                ):
                    try:
                        svc.uninstall_installable(name, version=current)
                    except RuntimeError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop(sess_key, None)
                        st.rerun()
            with cols[2]:
                if st.button("Refresh tags", key=f"refresh-{name}"):
                    installable.invalidate_versions_cache()
                    st.session_state.pop(_versions_key(name), None)
                    st.rerun()

        if in_flight:
            session = st.session_state[sess_key]
            current_step = session.view()

            if current_step.kind in (AcquireStateKind.COMPLETE, AcquireStateKind.FAILED, AcquireStateKind.CANCELLED):
                _render_step(current_step)
                if st.session_state.get(drop_key):
                    st.session_state.pop(sess_key, None)
                    st.session_state.pop(drop_key, None)
                    st.rerun()
                else:
                    st.session_state[drop_key] = True
                    if st.button("Dismiss", key=f"dismiss-{name}"):
                        st.session_state.pop(sess_key, None)
                        st.session_state.pop(drop_key, None)
                        st.rerun()
            else:
                render_target = st.empty()

                @st.fragment(run_every="2s")
                def _progress(
                    session=session, render_target=render_target, drop_key=drop_key
                ) -> None:
                    step = session.view()
                    with render_target.container():
                        _render_step(step)
                    if step.kind in (AcquireStateKind.COMPLETE, AcquireStateKind.FAILED, AcquireStateKind.CANCELLED) and not st.session_state.get(
                        drop_key
                    ):
                        st.session_state[drop_key] = True
                        st.rerun(scope="app")

                _progress()

                if st.button("Cancel", key=f"cancel-{name}"):
                    session.cancel()
                    st.rerun()
