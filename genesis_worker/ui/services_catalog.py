"""Service Catalog page — meta-view of every service the worker knows about.

Lists both enabled and disabled services (the dashboard and sidebar only
show the enabled subset; this page is the place to flip the bit). Rows
group by :class:`ServiceCategory` in iteration order, so a future
category addition is just a new enum value.

Each row carries:

- the service's display name + one-sentence description;
- a status badge (state of the underlying service);
- an ``st.toggle`` for enable/disable — disabled (greyed) when the
  service is currently running, mirroring the framework guard in
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

        for info in sorted(infos, key=lambda i: i.display_name.lower()):
            svc = worker.service(info.name)
            try:
                status = worker.service_status(info.name)
                state_value = status.state.value
            except Exception:  # noqa: BLE001 — diagnostic page; degrade
                state_value = "unavailable"

            with st.container(border=True):
                cols = st.columns([5, 2, 1])
                with cols[0]:
                    st.markdown(f"**{info.display_name}**")
                    st.caption(info.description or "(no description)")
                with cols[1]:
                    st.badge(state_value, color=_status_color(state_value))
                with cols[2]:
                    is_enabled = worker.services.is_enabled(info.name)
                    running = svc.is_running()
                    new_value = st.toggle(
                        "Enabled",
                        value=is_enabled,
                        # Greyed when running — matches the framework
                        # guard and stops users from accidentally
                        # bouncing a live service.
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
