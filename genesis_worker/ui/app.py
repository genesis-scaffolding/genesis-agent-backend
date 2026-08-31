"""Streamlit entry script for the Genesis Worker."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from genesis_worker import GenesisWorker

_FRAMEWORK_UI = Path(__file__).parent


@st.cache_resource
def get_worker() -> GenesisWorker:
    return GenesisWorker()


worker = get_worker()
st.session_state["worker"] = worker

st.set_page_config(
    page_title="Genesis Worker",
    layout="wide",
    page_icon=":material/settings:",
)


def _page(path: Path, title: str, icon: str, url_path: str | None) -> st.Page:
    return st.Page(str(path), title=title, icon=icon, url_path=url_path)


nav: dict[str, list[st.Page]] = {
    "Overview": [
        _page(_FRAMEWORK_UI / "dashboard.py", "Dashboard", ":material/dashboard:", None),
        _page(_FRAMEWORK_UI / "catalog.py", "Model Catalog", ":material/folder:", None),
        _page(_FRAMEWORK_UI / "services_catalog.py", "Service Catalog", ":material/apps:", None),
    ],
}

for svc_info in worker.list_enabled_services():
    svc = worker.service(svc_info.name)
    nav[svc_info.display_name] = [_page(p.path, p.label, p.icon, p.url_path) for p in svc.ui_pages]

for src_info in worker.list_sources():
    src = worker.source(src_info.name)
    nav[src_info.display_name] = [_page(p.path, p.label, p.icon, p.url_path) for p in src.ui_pages]

st.navigation(nav).run()
