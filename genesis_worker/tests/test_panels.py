"""Tests for the UI panel registry in ``utils/services/panels.py``."""

from __future__ import annotations

from typing import Any

import pytest

from genesis_worker.utils.services import panels


class _MockService:
    """Minimal stand-in for an ``InferenceService`` covering what panels read.

    Annotated ``# type: ignore[arg-type]`` at call sites; pyright
    doesn't know this fake matches the full ``InferenceService`` ABC
    (it doesn't carry the abstract lifecycle methods), but the
    panels only touch the read-only surface listed below.
    """

    def __init__(
        self,
        name: str = "mock",
        *,
        image_ref: str | None = "mock/image:latest",
        container_name: str | None = "mock-container",
        listen_address: str | None = "0.0.0.0:8080",
        auth_token: str | None = None,
        auth_enabled: bool = False,
        options_obj: Any = None,
        public_host: str = "host.local",
        web_ui_endpoint: str | None = None,
    ) -> None:
        self.name = name
        self.display_name = name.title()
        self._image_ref = image_ref
        self._container_name = container_name
        self._listen_address = listen_address
        self._auth_token = auth_token
        self._auth_enabled = auth_enabled
        self._options_obj = options_obj
        self._public_host = public_host
        self._web_ui_endpoint = web_ui_endpoint

    @property
    def image_ref(self) -> str | None:
        return self._image_ref

    @property
    def container_name(self) -> str | None:
        return self._container_name

    @property
    def listen_address(self) -> str | None:
        return self._listen_address

    def web_ui_endpoint(self) -> str | None:
        return self._web_ui_endpoint

    def public_host(self) -> str:
        return self._public_host

    def auth_token(self) -> str | None:
        return self._auth_token

    def auth_enabled(self) -> bool:
        return self._auth_enabled

    def is_available(self) -> bool:
        return True

    def is_running(self) -> bool:
        return False

    @property
    def options(self) -> Any:
        return self._options_obj


@pytest.fixture(autouse=True)
def _stub_streamlit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace streamlit calls with no-op recorders so panel code runs in tests."""
    record: dict[str, list] = {}

    def _container(*args, **kwargs):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            yield record.setdefault("containers", [])

        return _cm()

    def _markdown(text: str, *args, **kwargs):
        record.setdefault("markdown", []).append(text)

    def _header(text: str):
        record.setdefault("header", []).append(text)

    def _subheader(text: str):
        record.setdefault("subheader", []).append(text)

    def _columns(n: int):
        record.setdefault("columns", []).append(n)
        # Two-tuple of context managers: simple stand-ins.
        from contextlib import contextmanager

        @contextmanager
        def _col():
            yield record.setdefault("col", [])

        return (_col(), _col())

    monkeypatch.setattr(panels.st, "container", _container)
    monkeypatch.setattr(panels.st, "markdown", _markdown)
    monkeypatch.setattr(panels.st, "header", _header)
    monkeypatch.setattr(panels.st, "subheader", _subheader)
    monkeypatch.setattr(panels.st, "columns", _columns)
    # ``components.html`` triggers the real browser renderer otherwise.
    monkeypatch.setattr(
        panels.components, "html", lambda *a, **kw: record.setdefault("html", []).append(a)
    )

    # ``service_info`` reads ``st.session_state["worker"]`` for status;
    # supply a stub that returns a no-op status for every service.
    class _StubStatus:
        state = type("_S", (), {"value": "stopped"})()

    class _StubWorker:
        def service_status(self, name: str) -> Any:
            return _StubStatus()

    class _StubSession:
        def __getitem__(self, key: str) -> Any:
            if key == "worker":
                return _StubWorker()
            raise KeyError(key)

    monkeypatch.setattr(panels.st, "session_state", _StubSession())


# --- registry mechanics ----------------------------------------------------


def test_registered_kinds_lists_v1_panels() -> None:
    kinds = set(panels.registered_kinds())
    assert {"service_info", "container_info", "auth_token", "log_tail"}.issubset(kinds)


def test_register_duplicate_raises() -> None:
    with pytest.raises(ValueError, match="already registered"):
        panels.register("log_tail")(lambda svc, cfg: None)


def test_render_unknown_kind_raises() -> None:
    svc = _MockService()
    with pytest.raises(ValueError, match="unknown panel kind"):
        panels.render(svc, ["no_such_panel"], {})  # type: ignore[arg-type]


def test_render_calls_panels_in_declared_order() -> None:
    """Each renderer is invoked exactly once, in the order listed."""
    svc = _MockService()
    calls: list[str] = []

    @panels.register("__test_a")
    def _a(_svc, _cfg):
        calls.append("a")

    @panels.register("__test_b")
    def _b(_svc, _cfg):
        calls.append("b")

    try:
        panels.render(svc, ["__test_a", "__test_b"], {})  # type: ignore[arg-type]
        assert calls == ["a", "b"]
    finally:
        panels._REGISTRY.pop("__test_a", None)
        panels._REGISTRY.pop("__test_b", None)


# --- service_info ----------------------------------------------------------


def test_service_info_renders_without_auth_attrs() -> None:
    """``service_info`` doesn't reach for auth attributes the mock service lacks."""
    svc = _MockService()
    panels.render(svc, ["service_info"], {})  # type: ignore[arg-type]


def test_log_tail_renders_with_default_n_bytes() -> None:
    """``log_tail`` calls into ``render_tail_log`` (mocked below)."""
    svc = _MockService()
    captured: list[tuple[Any, int, str]] = []

    def _fake_tail(svc_arg, *, n_bytes, key):
        captured.append((svc_arg, n_bytes, key))

    import genesis_worker.utils.services.panels as _p

    monkey = _p.render_tail_log
    _p.render_tail_log = _fake_tail  # type: ignore[assignment]
    try:
        panels.render(svc, ["log_tail"], {})  # type: ignore[arg-type]
    finally:
        _p.render_tail_log = monkey  # type: ignore[assignment]

    assert captured == [(svc, 8 * 1024, svc.name)]


def test_log_tail_respects_panel_config_n_bytes() -> None:
    svc = _MockService()
    captured: list[int] = []

    def _fake_tail(svc_arg, *, n_bytes, key):
        captured.append(n_bytes)

    import genesis_worker.utils.services.panels as _p

    monkey = _p.render_tail_log
    _p.render_tail_log = _fake_tail  # type: ignore[assignment]
    try:
        panels.render(svc, ["log_tail"], {"n_bytes": 4096})  # type: ignore[arg-type]
    finally:
        _p.render_tail_log = monkey  # type: ignore[assignment]

    assert captured == [4096]
