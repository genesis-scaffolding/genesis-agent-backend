"""Navigation tests — a page is identified by its URL pathname, not its file.

The framework ships shared dispatcher pages: ``default_service_status.py``
and ``default_service_images.py`` are one file serving many services,
distinguished only by ``url_path``. ``st.switch_page`` given a file path
matches the registered pages by file path and takes the first match, so
every service's Admin button landed on whichever service registered the
shared file first. Routing by URL pathname fixes it — the pathname is
unique per page by Streamlit's own invariant.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from streamlit.source_util import page_icon_and_name
from streamlit.testing.v1 import AppTest

from genesis_worker import GenesisWorker
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.utils.state.enabled_services import save_enabled_set

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
FRAMEWORK_UI = PACKAGE_ROOT / "ui"
NAV_HELPER = PACKAGE_ROOT / "utils" / "ui" / "_nav.py"

# The framework pages app.py registers, parsed from app.py itself so the
# test cannot drift from the shell it guards.
_FRAMEWORK_PAGE_RE = re.compile(r'_page\(\s*_FRAMEWORK_UI\s*/\s*"([^"]+)"')


# --- the shared-dispatcher collision ----------------------------------------

_APP = '''"""Mini app: one shared page file registered under two URL pathnames."""

from pathlib import Path

import streamlit as st

_HERE = Path(__file__).parent
_SHARED = _HERE / "shared.py"

st.navigation(
    {
        "Overview": [
            st.Page(str(_HERE / "home.py"), title="Home", icon=":material/home:"),
            st.Page(
                str(_SHARED),
                title="Status A",
                icon=":material/monitor:",
                url_path="svc_a_status",
            ),
            st.Page(
                str(_SHARED),
                title="Status B",
                icon=":material/monitor:",
                url_path="svc_b_status",
            ),
        ]
    }
).run()
'''

_HOME = '''"""Buttons that navigate to the shared dispatcher page."""

from pathlib import Path

import streamlit as st

from genesis_worker.contracts import UiPage
from genesis_worker.utils.ui._nav import switch_to

_SHARED = Path(__file__).parent / "shared.py"

if st.button("Go to svc-a", key="go-a"):
    switch_to(UiPage("Status A", ":material/monitor:", _SHARED, url_path="svc_a_status"))
if st.button("Go to svc-b", key="go-b"):
    switch_to(UiPage("Status B", ":material/monitor:", _SHARED, url_path="svc_b_status"))
'''

_SHARED = '''"""Shared dispatcher — one file serving two URL pathnames."""

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

ctx = get_script_run_ctx()
info = ctx.pages_manager.get_pages().get(ctx.pages_manager.current_page_script_hash, {})
st.markdown(f"LANDED:{info.get('url_pathname', '?')}")
'''


def _write_mini_app(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(_APP)
    (tmp_path / "home.py").write_text(_HOME)
    (tmp_path / "shared.py").write_text(_SHARED)
    return tmp_path / "app.py"


def _landed_pathname(at: AppTest) -> str:
    body = "\n".join(str(m.value) for m in at.markdown)
    return body


@pytest.mark.parametrize(
    "button_key,expected",
    [("go-a", "svc_a_status"), ("go-b", "svc_b_status")],
)
def test_switch_to_lands_on_the_pages_own_url_path(
    tmp_path: Path, button_key: str, expected: str
) -> None:
    """Two pages share one file — each must be reachable by its own pathname.

    The path form of ``st.switch_page`` resolves the file and takes the
    first registrant, so the ``svc-b`` button would land on ``svc-a``.
    """
    app = _write_mini_app(tmp_path)
    at = AppTest.from_file(str(app), default_timeout=30)
    at.run()
    assert not at.exception, f"app crashed: {at.exception}"

    at.button(key=button_key).click()
    at.run()
    assert not at.exception, f"app crashed on navigation: {at.exception}"

    assert f"LANDED:{expected}" in _landed_pathname(at)


# --- the nav's pathnames are unique ----------------------------------------


def _worker_with_every_service_enabled(tmp_path: Path) -> GenesisWorker:
    """A worker whose enabled set covers every registered service."""
    state_dir = tmp_path / "state"
    settings = Settings(paths=PathsSettings(state_dir=state_dir))
    worker = GenesisWorker(settings=settings)
    save_enabled_set(state_dir, {info.name for info in worker.list_services()})
    return GenesisWorker(settings=Settings(paths=PathsSettings(state_dir=state_dir)))


def _nav_pages(worker: GenesisWorker) -> list[tuple[Path, str | None]]:
    """Every page app.py registers, as (path, url_path)."""
    app_source = (FRAMEWORK_UI / "app.py").read_text()
    pages: list[tuple[Path, str | None]] = [
        (FRAMEWORK_UI / name, None) for name in _FRAMEWORK_PAGE_RE.findall(app_source)
    ]
    for info in worker.list_enabled_services():
        for page in worker.service(info.name).ui_pages:
            pages.append((page.path, page.url_path))
    for info in worker.list_sources():
        for page in worker.source(info.name).ui_pages:
            pages.append((page.path, page.url_path))
    return pages


def _url_pathname(path: Path, explicit: str | None) -> str:
    """The URL pathname Streamlit registers for a page."""
    if explicit is not None:
        return explicit
    return page_icon_and_name(path)[1]


def test_nav_url_paths_are_unique(tmp_path: Path) -> None:
    """The nav the shell registers must have one pathname per page.

    Streamlit rejects a duplicate pathname at startup, so this is the
    invariant that makes pathname routing unambiguous — a collision is
    loud, not a silent misroute.
    """
    worker = _worker_with_every_service_enabled(tmp_path)
    pages = _nav_pages(worker)
    assert pages, "no pages discovered"

    pathnames = [_url_pathname(path, explicit) for path, explicit in pages]
    duplicates = {name for name in pathnames if pathnames.count(name) > 1}
    assert not duplicates, f"duplicate URL pathnames in the nav: {sorted(duplicates)}"


def _nav_app_source(pages: list[tuple[Path, str | None]]) -> str:
    """A runnable app that registers exactly these pages with ``st.navigation``."""
    entries = "\n".join(f"    ({str(path)!r}, {explicit!r})," for path, explicit in pages)
    return (
        "import streamlit as st\n"
        "\n"
        "_PAGES = [\n"
        f"{entries}\n"
        "]\n"
        "\n"
        "st.navigation(\n"
        "    {\n"
        '        "Overview": [\n'
        '            st.Page(path, title="Page", icon=":material/dashboard:", url_path=url_path)\n'
        "            for path, url_path in _PAGES\n"
        "        ]\n"
        "    }\n"
        ")\n"
    )


def _run_nav_app(tmp_path: Path, source: str) -> AppTest:
    """Write the generated app, prove it parses, then run it.

    A generated app that does not compile is logged by Streamlit as a
    script compilation error and never reaches the forward-message stream,
    so a broken generator would leave the test green. Parse it here.
    """
    try:
        ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(f"generated nav app does not parse: {exc}")
    app = tmp_path / "nav_app.py"
    app.write_text(source)
    at = AppTest.from_file(str(app), default_timeout=30)
    at.run()
    return at


def test_streamlit_accepts_the_nav(tmp_path: Path) -> None:
    """Streamlit itself must accept the nav — no duplicate pathname."""
    worker = _worker_with_every_service_enabled(tmp_path)
    pages = _nav_pages(worker)
    at = _run_nav_app(tmp_path, _nav_app_source(pages))
    assert not at.exception, f"st.navigation rejected the nav: {at.exception}"


def test_streamlit_rejects_a_duplicate_url_pathname(tmp_path: Path) -> None:
    """A duplicate pathname is a loud startup error, not a silent misroute.

    This is what makes pathname routing safe: the collision the file-path
    form hid — one dispatcher file serving many services — cannot be
    registered at all.
    """
    worker = _worker_with_every_service_enabled(tmp_path)
    pages: list[tuple[Path, str | None]] = [
        (path, "shared_status") for path, _ in _nav_pages(worker)
    ]
    at = _run_nav_app(tmp_path, _nav_app_source(pages))
    assert at.exception, "st.navigation accepted a nav with a duplicate URL pathname"
    assert "shared_status" in str(at.exception)


# --- call sites go through the helper ---------------------------------------


def _ui_modules() -> list[Path]:
    """Every module under a ``ui/`` directory, helpers included."""
    modules: list[Path] = []
    for ui_dir in PACKAGE_ROOT.rglob("ui"):
        if not ui_dir.is_dir() or "tests" in ui_dir.relative_to(PACKAGE_ROOT).parts:
            continue
        modules.extend(p for p in sorted(ui_dir.rglob("*.py")) if p.name != "__init__.py")
    return modules


def _switch_page_calls(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "switch_page"
    ]


def test_ui_call_sites_route_through_the_navigation_helper() -> None:
    """No UI module may navigate by file path — the helper is the only way.

    ``st.switch_page`` given a path matches registered pages by file path
    and takes the first match, which is how every service's Admin button
    ended up on the same page.
    """
    offenders: list[str] = []
    for module in _ui_modules():
        if module == NAV_HELPER:
            continue
        lines = _switch_page_calls(ast.parse(module.read_text()))
        if lines:
            offenders.append(f"{module.relative_to(PACKAGE_ROOT)}: lines {lines}")
    assert not offenders, (
        "UI modules must navigate through genesis_worker.utils.ui._nav.switch_to:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "ui/dashboard.py",
        "ui/catalog.py",
        "ui/settings.py",
        "services/llama_swap/ui/status.py",
    ],
)
def test_navigation_sites_use_the_helper(relative_path: str) -> None:
    """The pages that navigate must call ``switch_to``."""
    module = PACKAGE_ROOT / relative_path
    tree = ast.parse(module.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "switch_to"
    ]
    assert calls, f"{relative_path} does not navigate through switch_to"


def test_nav_helper_offers_switch_to_only() -> None:
    """The helper exposes ``switch_to``; the path helper is gone."""
    source = NAV_HELPER.read_text()
    assert "def switch_to(" in source
    assert "to_relative" not in source
