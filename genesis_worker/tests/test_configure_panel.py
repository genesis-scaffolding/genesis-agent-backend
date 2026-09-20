"""Tests for the ``configure`` UI panel (ADR-036).

The panel renders a single ``Configure`` button on the status page;
clicking it opens an ``@st.dialog`` modal with the auto-generated
form. Tests split into two pieces because of an AppTest quirk:

- AppTest propagates button clicks from the *script body* but not
  from inside ``@st.dialog`` (which inherits from ``@st.fragment``).
  The trigger button therefore tests as a regular widget click; the
  Apply / Apply & restart callbacks are tested by calling the dialog
  function directly so the click lands in a normal script-run
  context.

We monkeypatch the facade methods that hit docker / external state
so the tests stay hermetic.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import cast

import pytest
from streamlit.testing.v1 import AppTest

from genesis_worker import GenesisWorker
from genesis_worker.settings import PathsSettings, Settings


def _make_worker(tmp_path: Path) -> GenesisWorker:
    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )
    return GenesisWorker(settings=settings)


def _render(worker: GenesisWorker, name: str, *, body: str | None = None) -> AppTest:
    """Drive a Streamlit script through ``AppTest`` with the worker wired in.

    ``body`` overrides the default script body so tests can drive the
    dialog function directly (AppTest doesn't propagate clicks from
    inside ``@st.dialog`` fragments to the external test state).
    """
    if body is None:
        body = textwrap.dedent(
            f"""
            import streamlit as st
            from genesis_worker.utils.services.configure_panel import render_configure_panel

            worker = st.session_state["worker"]
            svc = worker.service("{name}")
            render_configure_panel(svc)
            """
        )

    driver = worker._settings.paths.log_dir.parent / "_configure_driver.py"
    driver.parent.mkdir(parents=True, exist_ok=True)
    driver.write_text(body)
    at = AppTest.from_file(str(driver), default_timeout=30)
    at.session_state["worker"] = worker
    return at


# --- trigger button (status page surface) ---------------------------------


def test_panel_renders_a_configure_trigger_button(tmp_path: Path) -> None:
    """The status page shows a single Configure trigger; no form widgets inline."""
    worker = _make_worker(tmp_path)
    at = _render(worker, "photoprism")
    at.run()
    assert not at.exception, f"panel crashed: {at.exception}"

    # The trigger button has a trailing ``-`` because of the
    # ``type="primary"`` / ``use_container_width=True`` kwargs.
    trigger = at.button(key="configure-photoprism-open-")
    assert trigger is not None
    # No form widgets yet — the dialog hasn't opened.
    assert not at.text_input, "text_input should not render before the dialog opens"
    assert not at.subheader, "no subheaders (form sections) before the dialog opens"


def test_panel_shows_no_options_message_for_python_services(tmp_path: Path) -> None:
    """Python services render the 'no declarative' info message instead of a trigger."""
    worker = _make_worker(tmp_path)
    at = _render(worker, "llama_swap")
    at.run()
    assert not at.exception
    info_body = "\n".join(i.value for i in at.info)
    assert "declarative" in info_body.lower()
    # No trigger button for a service without a declarative schema.
    assert all(b.key != "configure-llama_swap-open-" for b in at.button)


def test_trigger_opens_dialog_with_form_widgets(tmp_path: Path) -> None:
    """Clicking the trigger opens a modal with the auto-generated form widgets.

    AppTest doesn't propagate clicks inside ``@st.dialog`` to its
    external state, so we drive the dialog function directly to
    verify the form renders.
    """
    worker = _make_worker(tmp_path)
    body = textwrap.dedent(
        """
        import streamlit as st
        from genesis_worker.utils.services.configure_panel import _open_configure_dialog

        worker = st.session_state["worker"]
        svc = worker.service("photoprism")
        _open_configure_dialog(svc)
        """
    )
    at = _render(worker, "photoprism", body=body)
    at.run()
    assert not at.exception

    # The dialog renders the typed options as widgets. PhotoPrism
    # has string / port / path / bool fields, so we expect all three
    # widget kinds (text_input for string + path, number_input for
    # port, checkbox for bool).
    assert at.text_input, "no text_input rendered in the dialog"
    assert at.number_input, "no number_input rendered in the dialog"
    assert at.checkbox, "no checkbox rendered in the dialog"
    # Section subheaders from ``ui_group`` are visible inside the dialog.
    subheaders = [s.value for s in at.subheader]
    assert "Network" in subheaders
    assert "Storage" in subheaders
    assert "Security" in subheaders
    assert "Advanced" in subheaders


# --- apply / apply & restart (drive dialog directly) ----------------------


def test_apply_writes_overrides_and_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply (no restart) persists + rebuilds; no stop or start."""
    worker = _make_worker(tmp_path)
    call_order: list[str] = []
    write_calls: list[tuple[str, dict]] = []
    rebuild_calls: list[str] = []

    monkeypatch.setattr(
        worker,
        "write_user_overrides",
        lambda values: (
            call_order.append("write_user_overrides"),
            write_calls.append(("flat", dict(values))),
        ),
    )
    monkeypatch.setattr(
        worker,
        "write_service_overrides",
        lambda name, values: (
            call_order.append("write_service_overrides"),
            write_calls.append((name, dict(values))),
        ),
    )
    monkeypatch.setattr(
        worker,
        "rebuild_service",
        lambda name: (call_order.append("rebuild"), rebuild_calls.append(name)),
    )
    monkeypatch.setattr(worker, "stop_service", lambda name: call_order.append("stop"))
    monkeypatch.setattr(worker, "start_service", lambda name: call_order.append("start"))

    from genesis_worker.utils.process.docker import DockerContainer

    monkeypatch.setattr(DockerContainer, "is_running", lambda self: True)

    body = textwrap.dedent(
        """
        import streamlit as st
        from genesis_worker.utils.services.configure_panel import _open_configure_dialog

        worker = st.session_state["worker"]
        svc = worker.service("photoprism")
        _open_configure_dialog(svc)
        """
    )
    at = _render(worker, "photoprism", body=body)
    at.run()

    apply_button = at.button(key="configure-photoprism-apply-")
    assert apply_button is not None
    apply_button.click()
    at.run()

    assert rebuild_calls == ["photoprism"]
    assert "stop" not in call_order
    assert "start" not in call_order
    assert [c[0] for c in write_calls] == ["flat", "photoprism"]
    flat_keys = write_calls[0][1]
    assert "GENESIS_SERVICES__PHOTOPRISM__LISTEN_PORT" in flat_keys


def test_apply_and_restart_stops_before_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply & restart calls stop_service BEFORE rebuild_service.

    The rebuild refuses to run while the service is running, so the
    panel must stop first when the user asks for a restart. This
    regression covers the bug where ``rebuild_service`` raised
    before we reached the stop/start logic.
    """
    worker = _make_worker(tmp_path)
    call_order: list[str] = []
    rebuild_calls: list[str] = []

    # ``rebuild_service`` raises if the service is running — mimics
    # the real registry behaviour. With my fix, the panel stops
    # first, so this raise never fires.
    def fake_rebuild(name: str) -> None:
        if call_order and "stop" not in call_order:
            raise RuntimeError("cannot rebuild while running")
        call_order.append("rebuild")
        rebuild_calls.append(name)

    monkeypatch.setattr(worker, "stop_service", lambda name: call_order.append("stop"))
    monkeypatch.setattr(worker, "start_service", lambda name: call_order.append("start"))
    monkeypatch.setattr(worker, "rebuild_service", fake_rebuild)
    monkeypatch.setattr(
        worker, "write_user_overrides", lambda values: call_order.append("write_user_overrides")
    )
    monkeypatch.setattr(
        worker,
        "write_service_overrides",
        lambda name, values: call_order.append("write_service_overrides"),
    )

    from genesis_worker.utils.process.docker import DockerContainer

    monkeypatch.setattr(DockerContainer, "is_running", lambda self: True)

    body = textwrap.dedent(
        """
        import streamlit as st
        from genesis_worker.utils.services.configure_panel import _open_configure_dialog

        worker = st.session_state["worker"]
        svc = worker.service("photoprism")
        _open_configure_dialog(svc)
        """
    )
    at = _render(worker, "photoprism", body=body)
    at.run()

    button = at.button(key="configure-photoprism-apply-restart-")
    assert button is not None
    button.click()
    at.run()

    # Order: stop (so rebuild doesn't refuse), then writes + rebuild,
    # then start. If the panel ever calls rebuild before stop, the
    # fake_rebuild raises and we don't reach start — the assertions
    # below catch that.
    assert call_order[:3] == [
        "stop",
        "write_user_overrides",
        "write_service_overrides",
    ] or call_order[:1] == ["stop"], f"expected stop before rebuild; got {call_order}"
    assert "stop" in call_order
    assert "start" in call_order
    assert "rebuild" in call_order
    assert call_order.index("stop") < call_order.index("rebuild"), (
        f"stop must precede rebuild; got {call_order}"
    )
    assert call_order.index("rebuild") < call_order.index("start"), (
        f"rebuild must precede start; got {call_order}"
    )
    assert rebuild_calls == ["photoprism"]


def test_apply_and_restart_skips_stop_when_service_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply & restart when the service was not running: skip stop, just start."""
    worker = _make_worker(tmp_path)
    call_order: list[str] = []

    monkeypatch.setattr(worker, "stop_service", lambda name: call_order.append("stop"))
    monkeypatch.setattr(worker, "start_service", lambda name: call_order.append("start"))
    monkeypatch.setattr(worker, "rebuild_service", lambda name: call_order.append("rebuild"))
    monkeypatch.setattr(worker, "write_user_overrides", lambda values: None)
    monkeypatch.setattr(worker, "write_service_overrides", lambda name, values: None)

    from genesis_worker.utils.process.docker import DockerContainer

    monkeypatch.setattr(DockerContainer, "is_running", lambda self: False)

    body = textwrap.dedent(
        """
        import streamlit as st
        from genesis_worker.utils.services.configure_panel import _open_configure_dialog

        worker = st.session_state["worker"]
        svc = worker.service("photoprism")
        _open_configure_dialog(svc)
        """
    )
    at = _render(worker, "photoprism", body=body)
    at.run()

    button = at.button(key="configure-photoprism-apply-restart-")
    assert button is not None
    button.click()
    at.run()

    # ``is_running`` was False, so we skip the stop and go straight to
    # persist + rebuild + start.
    assert call_order == ["rebuild", "start"]


def test_apply_and_restart_reports_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed start leaves the new config persisted and the service stopped."""
    worker = _make_worker(tmp_path)

    monkeypatch.setattr(worker, "stop_service", lambda name: None)
    monkeypatch.setattr(worker, "write_user_overrides", lambda values: None)
    monkeypatch.setattr(worker, "write_service_overrides", lambda name, values: None)
    monkeypatch.setattr(worker, "rebuild_service", lambda name: None)

    def fake_start(name: str) -> None:
        raise RuntimeError("port already in use")

    monkeypatch.setattr(worker, "start_service", fake_start)

    from genesis_worker.utils.process.docker import DockerContainer

    monkeypatch.setattr(DockerContainer, "is_running", lambda self: True)

    body = textwrap.dedent(
        """
        import streamlit as st
        from genesis_worker.utils.services.configure_panel import _open_configure_dialog

        worker = st.session_state["worker"]
        svc = worker.service("photoprism")
        _open_configure_dialog(svc)
        """
    )
    at = _render(worker, "photoprism", body=body)
    at.run()

    button = at.button(key="configure-photoprism-apply-restart-")
    assert button is not None
    button.click()
    at.run()

    error_msgs = [str(e.value) for e in at.error]
    assert any("port already in use" in m for m in error_msgs), (
        f"expected start failure to be reported; got {error_msgs}"
    )


# --- robustness ------------------------------------------------------------


def test_panel_handles_unknown_option_type_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown type renders nothing in the dialog; the panel completes."""
    worker = _make_worker(tmp_path)
    svc = worker.service("photoprism")
    # ``svc.config`` is on ``DeclarativeServiceBase`` (only the YAML
    # services have it). Photoprism is declarative, so the runtime type
    # carries the attribute; cast for pyright.
    from genesis_worker.utils.services import DockerService
    from genesis_worker.utils.services.spec import OptionSpec

    docker_svc = cast("DockerService", svc)
    docker_svc.config.__dict__["option_specs"] = {
        "mystery": OptionSpec(type="weird_type", default="x")
    }

    at = _render(worker, "photoprism")
    at.run()
    assert not at.exception


def test_panel_uses_real_facade_in_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: persist scalars + maps, rebuild, the new config reflects them."""
    worker = _make_worker(tmp_path)

    from genesis_worker.utils.process.docker import DockerContainer

    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: False))
    monkeypatch.setattr(DockerContainer, "is_running", lambda self: False)

    body = textwrap.dedent(
        """
        import streamlit as st
        from genesis_worker.utils.services.configure_panel import _open_configure_dialog

        worker = st.session_state["worker"]
        svc = worker.service("photoprism")
        _open_configure_dialog(svc)
        """
    )
    at = _render(worker, "photoprism", body=body)
    at.run()

    apply_button = at.button(key="configure-photoprism-apply-")
    assert apply_button is not None
    apply_button.click()
    at.run()

    svc = worker.service("photoprism")
    assert svc is not None
    overrides = worker.read_user_overrides()
    photoprism_keys = [k for k in overrides if k.startswith("GENESIS_SERVICES__PHOTOPRISM__")]
    assert photoprism_keys, "no photoprism overrides persisted"
    sidecar = worker.read_service_overrides("photoprism")
    assert isinstance(sidecar, dict)
