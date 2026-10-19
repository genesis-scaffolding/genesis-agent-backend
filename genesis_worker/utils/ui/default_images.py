"""Pure renderer for the default declarative service images page.

Companion module to ``ui/default_service_images.py`` (the Streamlit
script that resolves the service from the URL). This module owns the
per-installable version picker: current state, tag selectbox,
install / update, uninstall, refresh, and live pull progress — the
same surface as the hand-crafted ComfyUI Images page, driven off the
generic ``ServiceInstall`` contract so every declarative docker and
compose service gets it for free.

Kept Streamlit-callable but script-free so tests can drive it through
``AppTest`` (see ``tests/test_default_images_page.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from ...contracts import (
    AcquireStateKind,
    AcquireView,
    InferenceService,
    InstallState,
    ServiceInstall,
)
from ..process import DockerContainer

_SESSION_KEY_PREFIX = "default-images/sessions"
_DROP_PENDING_PREFIX = "default-images/drop-pending"


@dataclass(frozen=True)
class ImageRow:
    """One installable's display state, computed without Streamlit."""

    installable: ServiceInstall
    ref: str | None  # effective ref (recorded selection beats default)
    present: bool  # effective ref exists locally
    version: str | None  # recorded version selection, None when unset
    source_url: str | None


def effective_ref(installable: ServiceInstall) -> str | None:
    """The ref the service runs with: recorded selection over the default tag.

    Non-docker installables (no ``image_ref`` attribute) report ``None``.
    """
    base = getattr(installable, "image_ref", None)
    if not isinstance(base, str):
        return None
    repo = base.rsplit(":", 1)[0] if ":" in base else base
    version = installable.installed_version()
    return f"{repo}:{version}" if version else base


def ref_present(installable: ServiceInstall) -> bool:
    """True iff the effective ref exists as a local docker image.

    Falls back to the installable's own state for non-docker installs.
    """
    ref = effective_ref(installable)
    if ref is None:
        return installable.state() == InstallState.INSTALLED
    return DockerContainer.image_present(ref)


def image_rows(svc: InferenceService) -> list[ImageRow]:
    return [
        ImageRow(
            installable=inst,
            ref=effective_ref(inst),
            present=ref_present(inst),
            version=inst.installed_version(),
            source_url=inst.source_url(),
        )
        for inst in svc.installs()
    ]


def _session_key(svc: InferenceService, name: str) -> str:
    return f"{_SESSION_KEY_PREFIX}/{svc.name}/{name}"


def _drop_pending_key(svc: InferenceService, name: str) -> str:
    return f"{_DROP_PENDING_PREFIX}/{svc.name}/{name}"


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
        st.progress(
            min(pct, 1.0), text=f"{step.title or 'fetching'} · {step.progress.bytes_done}/{total}"
        )
    else:
        st.info(step.title or step.kind)


def _render_one(svc: InferenceService, row: ImageRow) -> None:
    inst = row.installable
    sess_key = _session_key(svc, inst.name)
    drop_key = _drop_pending_key(svc, inst.name)
    in_flight = sess_key in st.session_state

    with st.expander(inst.name, expanded=False):
        state_label = "installed" if row.present else "not installed"
        resolved = row.ref or "—"
        st.write(f"State: **{state_label}** — resolved: **{resolved}**")
        if row.source_url:
            st.caption(f"Source: [{row.source_url}]({row.source_url})")
        if svc.is_running():
            st.caption(
                "Service is currently running — stop it from the Status page before uninstalling."
            )

        versions = inst.available_versions()
        if not versions:
            st.caption(
                "No tags returned from the registry (rate-limited or unreachable). "
                "Use **Refresh tags** to retry."
            )
        else:
            labels = [v.version for v in versions]
            try:
                idx = labels.index(row.version or "") if row.version else 0
            except ValueError:
                idx = 0
            choice = st.selectbox("Tag", labels, index=idx, key=f"tag-{svc.name}-{inst.name}")

            cols = st.columns(3)
            with cols[0]:
                if st.button(
                    "Install" if not row.present else "Update",
                    key=f"install-{svc.name}-{inst.name}",
                    disabled=in_flight,
                    help="An install is already in progress." if in_flight else None,
                ):
                    st.session_state[sess_key] = inst.install(version=choice)
                    st.rerun()
            with cols[1]:
                uninstall_disabled = svc.is_running() or row.version is None
                uninstall_help = (
                    "Stop the service first."
                    if svc.is_running()
                    else "No version recorded."
                    if row.version is None
                    else None
                )
                uninstall = getattr(svc, "uninstall_installable", None)
                if st.button(
                    "Uninstall",
                    key=f"uninstall-{svc.name}-{inst.name}",
                    disabled=uninstall_disabled or not callable(uninstall),
                    help=uninstall_help,
                ) and callable(uninstall):
                    try:
                        uninstall(inst.name, version=row.version)
                    except RuntimeError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop(sess_key, None)
                        st.rerun()
            with cols[2]:
                if st.button("Refresh tags", key=f"refresh-{svc.name}-{inst.name}"):
                    invalidate = getattr(inst, "invalidate_versions_cache", None)
                    if callable(invalidate):
                        invalidate()
                    st.rerun()

        if in_flight:
            session = st.session_state[sess_key]
            current_step = session.view()

            if current_step.kind in (
                AcquireStateKind.COMPLETE,
                AcquireStateKind.FAILED,
                AcquireStateKind.CANCELLED,
            ):
                _render_step(current_step)
                if st.session_state.get(drop_key):
                    st.session_state.pop(sess_key, None)
                    st.session_state.pop(drop_key, None)
                    st.rerun()
                else:
                    st.session_state[drop_key] = True
                    if st.button("Dismiss", key=f"dismiss-{svc.name}-{inst.name}"):
                        st.session_state.pop(sess_key, None)
                        st.session_state.pop(drop_key, None)
                        st.rerun()
            else:

                @st.fragment(run_every="2s")
                def _progress(session=session, drop_key=drop_key) -> None:
                    step = session.view()
                    _render_step(step)
                    if step.kind in (
                        AcquireStateKind.COMPLETE,
                        AcquireStateKind.FAILED,
                        AcquireStateKind.CANCELLED,
                    ) and not st.session_state.get(drop_key):
                        st.session_state[drop_key] = True
                        st.rerun(scope="app")

                _progress()

                if st.button("Cancel", key=f"cancel-{svc.name}-{inst.name}"):
                    session.cancel()
                    st.rerun()


def render_default_images(svc: InferenceService) -> None:
    """Render the Images page for ``svc`` — one section per installable."""
    st.title(f"{svc.display_name} — Images")
    st.caption(
        "Every image this service runs. Pick a tag, **Install** to pull it, "
        "**Uninstall** to remove a pulled image. The running service is "
        "restarted with the selected version from the Status page."
    )

    rows = image_rows(svc)
    if not rows:
        st.info("This service has no installables.")
        return
    for row in rows:
        _render_one(svc, row)


__all__ = [
    "ImageRow",
    "effective_ref",
    "image_rows",
    "ref_present",
    "render_default_images",
]
