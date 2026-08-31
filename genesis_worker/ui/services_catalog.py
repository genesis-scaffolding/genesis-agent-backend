"""Service Catalog page — meta-view of every service the worker knows about.

Lists both enabled and disabled services (the dashboard and sidebar only
show the enabled subset; this page is the place to flip the bit). Rows
group by :class:`ServiceCategory` in iteration order, so a future
category addition is just a new enum value.

Layout (ADR-029):

- one bordered container per category, with a subheader and a column
  header row;
- inside the container, services render as compact rows (``st.columns``
  with top-aligned cells, no per-row border) — the name + description
  occupy the wide left column, the status badge the middle, and a
  toggle the right. Top-alignment prevents the toggle cell from
  inflating the row's height when its neighbours have shorter content;
- the toggle is greyed (``disabled=True``) when the service is
  currently running, mirroring the framework guard in
  ``ServiceRegistry.disable``.

Toggling mutates the registry immediately and ``st.rerun()``s so the
next render shows the new state without a manual refresh.
"""

from __future__ import annotations

from typing import Literal

import streamlit as st

from genesis_worker.contracts import ServiceCategory

worker = st.session_state["worker"]


# Display labels for each category. Local to this page — the framework
# contract stays the enum, so this stays a UI concern.
_CATEGORY_LABELS: dict[ServiceCategory, str] = {
    ServiceCategory.LLM: "LLM inference",
    ServiceCategory.IMAGE: "Image generation",
    ServiceCategory.CHAT: "Chat UIs",
    ServiceCategory.CRAWLER: "Web crawlers",
    ServiceCategory.MEDIA: "Media servers",
    ServiceCategory.UTILITY: "Utilities",
    ServiceCategory.OTHER: "Other",
}

BadgeColor = Literal["green", "blue", "red", "gray"]


def _status_color(state: str) -> BadgeColor:
    if state in ("running", "available"):
        return "green"
    if state in ("starting", "stopping"):
        return "blue"
    if state == "failed":
        return "red"
    return "gray"


# Column widths used for both the header row and the per-service rows.
# The wide left column holds name + description; the middle is just the
# status badge; the right is the toggle in its compact on/off switch
# form (label collapsed).
_COLS = [5, 2, 1]


def _render_header_row() -> None:
    """Bold column titles — sits at the top of each category container."""
    cols = st.columns(_COLS, vertical_alignment="top")
    cols[0].markdown("**Service**")
    cols[1].markdown("**Status**")
    cols[2].markdown(
        "**Enabled**",
        help="Show this service on the dashboard and sidebar.",
    )


def _render_service_row(info) -> None:
    """One row in the table: name+description, status, toggle.

    No ``st.container(border=True)`` — the bordered category container
    is the only border on the page, and rows rely on alignment with the
    header for visual structure. ``vertical_alignment="top"`` keeps the
    toggle cell flush with the row's content rather than stretching to
    match the badge column.
    """
    svc = worker.service(info.name)
    try:
        status = worker.service_status(info.name)
        state_value = status.state.value
    except Exception:  # noqa: BLE001 — diagnostic page; degrade
        state_value = "unavailable"

    cols = st.columns(_COLS, vertical_alignment="top")
    with cols[0]:
        st.markdown(f"**{info.display_name}**")
        if info.description:
            st.caption(info.description)
    with cols[1]:
        st.badge(state_value, color=_status_color(state_value))
    with cols[2]:
        is_enabled = worker.services.is_enabled(info.name)
        running = svc.is_running()
        new_value = st.toggle(
            "Enabled",
            value=is_enabled,
            # Greyed when running — matches the framework guard and
            # stops users from accidentally bouncing a live service.
            disabled=running,
            key=f"enable-{info.name}",
            label_visibility="collapsed",
            help=(
                "Stop the service before disabling."
                if running
                else "Show on the dashboard and sidebar."
            ),
        )
        if new_value != is_enabled:
            if new_value:
                worker.services.enable(info.name)
            else:
                worker.services.disable(info.name)
            st.rerun()


st.title("Service Catalog")
st.caption(
    "Enable / disable the services that appear on the dashboard and sidebar. "
    "Disabled services are hidden from those surfaces and can only be "
    "re-enabled here."
)

# All services — disabled ones are part of this view by design.
all_services = worker.list_services()
if not all_services:
    st.info("No services registered.")
    st.stop()

# Group by category in iteration order so the visual order is stable
# even when categories are empty (they just don't render).
services_by_category: dict[ServiceCategory, list] = {}
for info in all_services:
    services_by_category.setdefault(info.category, []).append(info)


for category in ServiceCategory:
    infos = services_by_category.get(category, [])
    if not infos:
        continue

    with st.container(border=True):
        st.subheader(_CATEGORY_LABELS[category])
        _render_header_row()
        st.divider()
        for info in sorted(infos, key=lambda i: i.display_name.lower()):
            _render_service_row(info)
