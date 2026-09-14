"""Framework Settings page — review and override the worker's effective config (ADR-034).

Exposes framework knobs only:

- **Paths** — ``vault_path`` and the five XDG dirs.
- **Per-source ``local_path``** — ``huggingface``, ``lmstudio``.

Service-specific knobs (``listen_addr``, ``llama_server_variant``,
``kv_quant_over_bytes``, etc.) stay on each service's own Status
page. This page deliberately does not duplicate them — a unified
form would grow into a maintenance burden as plugins evolve, and
each service's own page already has a focused surface for its
options.

The page writes the override file atomically and calls
``worker.refresh_config()`` to rebuild internal state in place. No
restart of the worker process is required; running services are
unaffected (their status queries re-check tmux/docker on every
call, but new options don't reach a running process until it's
restarted on its own Status page).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

import streamlit as st

from genesis_worker.utils.config_overrides import read_user_overrides
from genesis_worker.utils.models import SettingSnapshot

worker = st.session_state["worker"]

# --- helpers ---------------------------------------------------------------
# Defined above their callers so pyright sees them as in-scope; Streamlit
# only runs the script body, not the helper definitions, so ordering is
# purely a type-checking concern here.

_BadgeColor = Literal["green", "blue", "red", "gray"]
_SOURCE_BADGE: dict[str, tuple[str, _BadgeColor]] = {
    "env": ("env", "green"),
    "user_overrides": ("override", "blue"),
    "dotenv": (".env", "gray"),
    "default": ("default", "gray"),
    "constructor": ("explicit", "gray"),
}


def _render_snapshot_table(snapshots: list[SettingSnapshot]) -> None:
    """One row per snapshot: name, resolved value, source badge."""
    for snapshot in snapshots:
        cols = st.columns([3, 4, 2], vertical_alignment="top")
        with cols[0]:
            st.markdown(f"`{snapshot.name}`")
        with cols[1]:
            st.markdown(f"`{snapshot.value}`")
        with cols[2]:
            label, color = _SOURCE_BADGE.get(snapshot.source, (snapshot.source, "gray"))
            st.badge(label, color=color)


def _render_override_row(
    snapshot: SettingSnapshot,
    existing_overrides: dict[str, str],
    pending: dict[str, str],
) -> None:
    """One editable row. Checkbox gates the input; input is bound to the pending dict."""
    is_overridden = snapshot.override_key in existing_overrides
    initial_value = existing_overrides.get(snapshot.override_key, str(snapshot.value))

    cols = st.columns([1, 3, 5], vertical_alignment="top")
    with cols[0]:
        enabled = st.checkbox(
            "Override",
            value=is_overridden,
            key=f"settings-enable-{snapshot.name}",
            label_visibility="collapsed",
        )
    with cols[1]:
        st.markdown(f"`{snapshot.override_key}`")
    with cols[2]:
        if enabled:
            value = st.text_input(
                "Value",
                value=initial_value,
                key=f"settings-value-{snapshot.name}",
                label_visibility="collapsed",
            )
            pending[snapshot.override_key] = value


def _format_existing_extras(
    existing_overrides: dict[str, str],
    known_keys: set[str],
) -> str:
    """Render the existing override keys that aren't on a known row."""
    extras = {k: v for k, v in existing_overrides.items() if k not in known_keys}
    if not extras:
        return ""
    return "\n".join(f"{k}={v}" for k, v in sorted(extras))


def _parse_extra_overrides(text: str) -> dict[str, str]:
    """Parse the textarea using the same dotenv reader as the file.

    Lines without ``=`` raise from the reader; we surface that as an
    inline warning rather than failing the whole save — the user can
    fix it before re-saving.
    """
    text = text.strip()
    if not text:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write(text + "\n")
        tmp_path = Path(f.name)
    try:
        return read_user_overrides(tmp_path)
    except ValueError as exc:
        st.error(f"Extra overrides parse error: {exc}")
        return {}
    finally:
        tmp_path.unlink(missing_ok=True)


# --- Page body --------------------------------------------------------------

st.title("Settings")
st.caption(
    "Review the worker's resolved config and override individual knobs. "
    "Changes take effect on save without restarting the worker process."
)

with st.container(border=True):
    st.header("Effective settings")
    st.caption(
        "One row per framework knob. The 'Source' column tells you which "
        "precedence layer the resolved value came from."
    )
    snapshots = worker.snapshot_settings()
    if not snapshots:
        st.info("No settings to display.")
    else:
        _render_snapshot_table(snapshots)


with st.container(border=True):
    st.header("User overrides")
    st.caption(
        "Tick 'Override' to set a value, then click 'Save overrides'. "
        "Untick to remove that key from the override file. "
        "Unknown keys in 'Extra overrides' are preserved verbatim."
    )

    snapshots = worker.snapshot_settings()
    existing_overrides = worker.read_user_overrides()

    pending: dict[str, str] = {}
    for snapshot in snapshots:
        _render_override_row(snapshot, existing_overrides, pending)

    st.divider()
    st.subheader("Extra overrides")
    st.caption(
        "One KEY=VALUE per line. Use this for framework knobs we don't "
        "have explicit fields for (e.g. service-specific options). "
        "Unknown keys land in the override file unchanged."
    )
    extra_text = st.text_area(
        "Extra overrides",
        value=_format_existing_extras(existing_overrides, {s.override_key for s in snapshots}),
        key="settings-extra-overrides",
        height=120,
        label_visibility="collapsed",
    )

    pending.update(_parse_extra_overrides(extra_text))

    st.divider()

    save_col, _ = st.columns([1, 3])
    with save_col:
        save_clicked = st.button(
            "Save overrides",
            key="settings-save-overrides",
            type="primary",
            use_container_width=True,
        )

    if save_clicked:
        try:
            worker.write_user_overrides(pending)
        except ValueError as exc:
            st.error(f"Save refused: {exc}")
        else:
            try:
                worker.refresh_config()
            except Exception as exc:  # noqa: BLE001 — surface inline
                st.error(
                    f"Overrides written to disk but the worker could not pick them up: {exc}. "
                    "Your previous working state is unchanged — fix the override file by hand."
                )
            else:
                st.success(
                    f"Saved {len(pending)} override key(s). "
                    "Changes are live for paths and source discovery."
                )
                st.rerun()


with st.container(border=True):
    st.header("Status")
    running = [s for s in worker.list_enabled_services() if worker.service(s.name).is_running()]
    if not running:
        st.caption("No services are running — no restart needed.")
    else:
        st.warning(
            f"{len(running)} service(s) are running. They will continue with "
            "their current options until restarted on their own page; only "
            "paths and source discovery pick up new values automatically."
        )
        for info in running:
            svc = worker.service(info.name)
            pages = svc.ui_pages
            if pages:
                cols = st.columns([3, 1])
                with cols[0]:
                    st.markdown(f"**{info.display_name}**")
                with cols[1]:
                    if st.button(
                        "Open status →",
                        key=f"settings-open-{info.name}",
                        use_container_width=True,
                    ):
                        from genesis_worker.utils.ui._nav import to_relative

                        st.switch_page(to_relative(pages[0].path))


__all__ = []
