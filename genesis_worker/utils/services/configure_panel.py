"""Auto-generated ``configure`` panel for declarative services.

The panel renders a Streamlit form from a service's ``config.option_specs``
(the ``OptionSpec`` dict the loader preserves from the YAML). Each
option gets the right widget for its declared type; ``ui_label``,
``ui_help``, and ``ui_group`` drive the visual layout. Persisting
calls into the worker facade so the file format and rebuild flow
stay in one place.

The panel is the front half of ADR-036: the user changes values
on the form, the panel writes to the right persistence layer
(scalar options → ``user-overrides.env``, map options → the
per-service JSON sidecar), then asks the worker to rebuild the
in-memory service instance. The running container, if any, is
untouched until the user clicks ``Apply & restart`` or restarts
manually on the service's start/stop controls.

Tests drive :func:`render_configure_panel` against a fake worker
that records calls; the form rendering itself uses ``streamlit``
mock helpers. See ``tests/test_configure_panel.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from ...contracts import InferenceService
from .base import DeclarativeServiceBase
from .spec import OptionSpec

# Widget key prefix. Per-service, per-option, per-row keys avoid
# collisions when the same panel renders for two services or when
# Streamlit reruns share ``st.session_state`` across navigation.
_KEY_PREFIX = "configure"


def _widget_key(svc_name: str, option_name: str, suffix: str = "") -> str:
    return f"{_KEY_PREFIX}-{svc_name}-{option_name}-{suffix}"


def _section_key(svc_name: str, group: str) -> str:
    return f"{_KEY_PREFIX}-{svc_name}-section-{group}"


# --- widget rendering ------------------------------------------------------


def _render_scalar(svc: InferenceService, name: str, spec: OptionSpec, current: Any) -> Any:
    """Render a scalar (string / int / float / bool / port / path) widget.

    Returns the parsed value the form just submitted. Streamlit reruns
    the script on every interaction, so this is called once per
    rerun; the widget state survives via the explicit ``key`` we
    pass.
    """
    label = spec.ui_label or name
    help_text = spec.ui_help or None
    key = _widget_key(svc.name, name)

    if spec.type == "bool":
        return st.checkbox(label, value=bool(current), key=key, help=help_text)

    if spec.type == "port":
        # ``port`` carries an int constraint; number_input with min/max
        # matches the constraint at the widget level. The pydantic
        # field re-validates at Apply time.
        return st.number_input(
            label,
            value=int(current) if isinstance(current, int) else 0,
            min_value=1,
            max_value=65535,
            step=1,
            key=key,
            help=help_text,
        )

    if spec.type == "int":
        return st.number_input(
            label,
            value=int(current) if isinstance(current, int) else 0,
            step=1,
            key=key,
            help=help_text,
        )

    if spec.type == "float":
        return st.number_input(
            label,
            value=float(current) if isinstance(current, (int, float)) else 0.0,
            step=0.1,
            format="%.4f",
            key=key,
            help=help_text,
        )

    if spec.type == "string":
        return st.text_input(
            label,
            value=str(current) if current is not None else "",
            key=key,
            help=help_text,
        )

    if spec.type == "path":
        return st.text_input(
            label,
            value=str(current) if current is not None else "",
            key=key,
            help=help_text,
        )

    return None  # Unknown scalar type; form skips it.


def _render_list(svc: InferenceService, name: str, spec: OptionSpec, current: Any) -> Any:
    """Render a list-typed widget. Comma-separated text for v1 simplicity."""
    label = spec.ui_label or name
    help_text = spec.ui_help or None
    key = _widget_key(svc.name, name)

    items = list(current) if isinstance(current, list) else []
    if spec.type == "list[string]":
        text = ", ".join(str(x) for x in items)
        edited = st.text_input(label, value=text, key=key, help=help_text)
        return [s.strip() for s in edited.split(",") if s.strip()]
    if spec.type == "list[int]":
        text = ", ".join(str(x) for x in items)
        edited = st.text_input(label, value=text, key=key, help=help_text)
        out: list[int] = []
        for chunk in edited.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                out.append(int(chunk))
            except ValueError:
                st.warning(f"{name}: ignoring non-integer {chunk!r}")
        return out
    if spec.type == "list[path]":
        text = "\n".join(str(x) for x in items)
        edited = st.text_area(label, value=text, key=key, help=help_text)
        return [line for line in edited.splitlines() if line.strip()]
    return None


def _render_env_map(svc: InferenceService, name: str, spec: OptionSpec, current: Any) -> Any:
    """Key/value row editor for ``env_map`` options.

    Rows live in ``st.session_state`` so they survive reruns. The
    ``Add row`` / per-row ``Remove`` buttons mutate the row list
    in place; ``st.rerun`` re-renders the form with the new rows.
    """
    rows_key = _widget_key(svc.name, name, "rows")
    label = spec.ui_label or name
    help_text = spec.ui_help or None
    if rows_key not in st.session_state:
        # Seed from the current value (ctx.options merge → typed
        # options instance → ``extra_env`` is a ``dict[str, str]``).
        existing = dict(current) if isinstance(current, Mapping) else {}
        st.session_state[rows_key] = [{"k": k, "v": str(v)} for k, v in existing.items()]

    rows = st.session_state[rows_key]
    if st.button(f"Add {label} row", key=_widget_key(svc.name, name, "add")):
        rows.append({"k": "", "v": ""})
        st.rerun()

    new_rows: list[dict[str, str]] = []
    for i, row in enumerate(rows):
        cols = st.columns([3, 5, 1])
        with cols[0]:
            k = (
                st.text_input(
                    "Key", value=row.get("k", ""), key=_widget_key(svc.name, name, f"k-{i}")
                )
                or ""
            )
        with cols[1]:
            v = (
                st.text_input(
                    "Value", value=row.get("v", ""), key=_widget_key(svc.name, name, f"v-{i}")
                )
                or ""
            )
        with cols[2]:
            if st.button("Remove", key=_widget_key(svc.name, name, f"rm-{i}")):
                # Drop this row from the displayed list; rerun rebuilds.
                rows.pop(i)
                st.rerun()
        if k:
            new_rows.append({"k": k, "v": v})
    st.session_state[rows_key] = new_rows
    if help_text:
        st.caption(help_text)
    return {r["k"]: r["v"] for r in new_rows}


def _render_mount_map(svc: InferenceService, name: str, spec: OptionSpec, current: Any) -> Any:
    """Two-column row editor for ``mount_map`` options (container → host)."""
    rows_key = _widget_key(svc.name, name, "rows")
    label = spec.ui_label or name
    help_text = spec.ui_help or None
    if rows_key not in st.session_state:
        existing = dict(current) if isinstance(current, Mapping) else {}
        st.session_state[rows_key] = [
            {"container": str(c), "host": str(h)} for c, h in existing.items()
        ]

    rows = st.session_state[rows_key]
    if st.button(f"Add {label} row", key=_widget_key(svc.name, name, "add")):
        rows.append({"container": "", "host": ""})
        st.rerun()

    new_rows: list[dict[str, str]] = []
    for i, row in enumerate(rows):
        cols = st.columns([3, 5, 1])
        with cols[0]:
            c_path = (
                st.text_input(
                    "Container path",
                    value=row.get("container", ""),
                    key=_widget_key(svc.name, name, f"c-{i}"),
                )
                or ""
            )
        with cols[1]:
            h_path = (
                st.text_input(
                    "Host path",
                    value=row.get("host", ""),
                    key=_widget_key(svc.name, name, f"h-{i}"),
                )
                or ""
            )
        with cols[2]:
            if st.button("Remove", key=_widget_key(svc.name, name, f"rm-{i}")):
                rows.pop(i)
                st.rerun()
        if c_path and h_path:
            new_rows.append({"container": c_path, "host": h_path})
    st.session_state[rows_key] = new_rows
    if help_text:
        st.caption(help_text)
    return {r["container"]: r["host"] for r in new_rows}


def _render_option(svc: InferenceService, name: str, spec: OptionSpec, current: Any) -> Any:
    """Dispatch to the per-type widget. Returns the submitted value or ``None``."""
    if spec.type in {"string", "int", "float", "bool", "port", "path"}:
        return _render_scalar(svc, name, spec, current)
    if spec.type in {"list[string]", "list[int]", "list[path]"}:
        return _render_list(svc, name, spec, current)
    if spec.type == "env_map":
        return _render_env_map(svc, name, spec, current)
    if spec.type == "mount_map":
        return _render_mount_map(svc, name, spec, current)
    return None


# --- persistence -----------------------------------------------------------


def _persist(
    worker: Any,
    svc: InferenceService,
    scalars: Mapping[str, Any],
    maps: Mapping[str, Any],
) -> None:
    """Write the form values to the right persistence layer.

    Scalar options land in ``user-overrides.env`` under the
    ``GENESIS_SERVICES__<NAME>__<OPTION>`` naming the Settings page
    already understands. Map options land in the per-service JSON
    sidecar. The merge order inside ``Settings.options_for`` is
    scalars-first, maps-second, so the sidecar wins for keys that
    collide (ADR-036).
    """
    flat: dict[str, str] = {}
    for key, value in scalars.items():
        namespaced = f"GENESIS_SERVICES__{svc.name.upper()}__{key.upper()}"
        if isinstance(value, bool):
            flat[namespaced] = "true" if value else "false"
        elif value is None:
            continue
        else:
            flat[namespaced] = str(value)
    worker.write_user_overrides(flat)
    worker.write_service_overrides(svc.name, dict(maps))


# --- top-level --------------------------------------------------------------


def render_configure_panel(svc: InferenceService) -> None:
    """Render a ``Configure`` button that opens the form in a modal dialog.

    The status page stays focused on operational info (install /
    start / stop, container state, logs). The form is dense and
    only relevant when the operator wants to change settings, so it
    lives behind a modal trigger rather than dumping inline on the
    page (ADR-036).
    """
    if not isinstance(svc, DeclarativeServiceBase):
        st.info("This service does not expose a declarative options schema.")
        return
    option_specs: Mapping[str, OptionSpec] = svc.config.option_specs
    if not option_specs:
        st.info("This service exposes no user-editable options.")
        return

    if st.button(
        "Configure",
        key=_widget_key(svc.name, "open"),
        type="primary",
        use_container_width=True,
    ):
        _open_configure_dialog(svc)


@st.dialog("Configure", width="large")
def _open_configure_dialog(svc: InferenceService) -> None:
    """Modal form. Reads / writes the same persistence layer as before.

    The dialog owns the form widgets and the ``Apply`` /
    ``Apply & restart`` actions. Closing the dialog (via successful
    ``Apply & restart`` triggering a rerun, or the user closing it
    manually) discards the in-progress form state — there is no
    draft persistence, which matches what the inline form did
    (Streamlit reruns on every interaction; widget state survives
    via explicit keys, not via dialog scoping).
    """
    worker = st.session_state["worker"]
    if not isinstance(svc, DeclarativeServiceBase):
        st.info("This service does not expose a declarative options schema.")
        return
    option_specs: Mapping[str, OptionSpec] = svc.config.option_specs
    current_options = svc.options

    by_group: dict[str, list[tuple[str, OptionSpec]]] = {}
    for name, spec in option_specs.items():
        by_group.setdefault(spec.ui_group or "General", []).append((name, spec))

    scalars: dict[str, Any] = {}
    maps: dict[str, Any] = {}
    for group in sorted(by_group):
        st.subheader(group)
        for name, spec in by_group[group]:
            current = getattr(current_options, name, spec.default)
            value = _render_option(svc, name, spec, current)
            if value is None:
                continue
            if spec.type in {"env_map", "mount_map"}:
                maps[name] = value
            else:
                scalars[name] = value

    cols = st.columns(2)
    with cols[0]:
        apply_clicked = st.button(
            "Apply",
            key=_widget_key(svc.name, "apply"),
            type="primary",
            use_container_width=True,
        )
    with cols[1]:
        apply_restart_clicked = st.button(
            "Apply & restart",
            key=_widget_key(svc.name, "apply-restart"),
            use_container_width=True,
        )

    if apply_clicked or apply_restart_clicked:
        _handle_apply(
            worker,
            svc,
            scalars,
            maps,
            also_restart=apply_restart_clicked,
        )


def _handle_apply(
    worker: Any,
    svc: InferenceService,
    scalars: Mapping[str, Any],
    maps: Mapping[str, Any],
    *,
    also_restart: bool,
) -> None:
    """Persist form values, rebuild the in-memory service, optionally restart.

    Order matters: if the user asked for a restart and the service is
    running, we **stop first** so the subsequent rebuild doesn't
    refuse (the rebuild check rejects rebuilds of running services).
    Then persist + rebuild, then start. If the start fails, the
    config stays persisted and the service stays stopped — no
    rollback (ADR-036, decision 4).

    Construct-first, swap-last: the persist + rebuild steps run
    before the restart. If persist or rebuild raises, the file may
    or may not be on disk; the running container keeps its old
    config until the user fixes the bad input and retries.
    """
    was_running = svc.is_running()

    # Stop first when we're going to restart and the service is
    # currently running — the rebuild refuses otherwise.
    if also_restart and was_running:
        try:
            worker.stop_service(svc.name)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not stop the service before applying: {exc}")
            return

    try:
        _persist(worker, svc, scalars, maps)
        worker.rebuild_service(svc.name)
    except Exception as exc:  # noqa: BLE001 — surface inline
        st.error(f"Apply failed: {exc}")
        return

    if not also_restart:
        if was_running:
            st.success(
                "Saved. The service is still running with the old config — restart to apply."
            )
        else:
            st.success("Saved.")
        return

    try:
        worker.start_service(svc.name)
    except Exception as exc:  # noqa: BLE001
        st.error(
            f"Config saved; restart failed: {exc}. "
            "The service is stopped with the new config — start it manually."
        )
        return

    st.success("Saved and restarted.")
    st.rerun()


__all__ = ["render_configure_panel"]
