"""CLI flags editor for the ComfyUI container (ADR-041).

Lets the operator edit ``ComfyUiOptions.extra_args`` from the UI. Each
non-blank line in the text area becomes one CLI flag; flags are
appended after the bake-in ``--models-directory /vault/comfyui`` and
the YAML default ``--verbose``. Save persists the value to the
per-service JSON sidecar via ``worker.write_service_overrides``; Save
& restart also stops and starts the running container so the new
flags take effect immediately.

The ComfyUI command line is argument-style (``--foo --bar=value``),
not positional. The form renders one flag per line so the user can
read and edit the set easily. Empty lines and whitespace-only lines
are skipped.

Notes surfaced to the operator:
- ``--models-directory /vault/comfyui`` is hardcoded in ``service.py``
  before user flags. To override the models path, add your own
  ``--models-directory <other>`` at the end of the list; ComfyUI's
  argparse takes last-wins for duplicate flags.
- An empty text area reverts to the YAML default (``["--verbose"]``)
  on the next worker restart, since the sidecar key is removed.
"""

from __future__ import annotations

import streamlit as st

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title("Flags")
st.caption(
    "CLI flags appended after the bake-in `--models-directory /vault/comfyui`. "
    "Saved flags persist across worker restarts; Save & restart also applies "
    "them to the running container."
)

_ERROR_KEY = "flags-start_error"


def _parse_args(raw: str) -> list[str]:
    """Split the text area into a list of flags, dropping blank lines."""
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


# Pull the current value from the sidecar when available so the form
# reflects what's actually persisted, not just the in-memory default.
# ``worker.read_service_overrides`` returns the full sidecar dict;
# the in-memory value is the fallback when the sidecar is empty.
sidecar = worker.read_service_overrides(SERVICE_NAME)
persisted = sidecar.get("extra_args")
current_value = persisted if persisted is not None else svc._options.extra_args


raw = st.text_area(
    "Extra flags (one per line)",
    key="flags-text-area",
    value="\n".join(current_value),
    height=240,
    help=(
        "Each line is one CLI flag. Empty lines are ignored. "
        "Example: `--lowvram --gpu-only --disable-metadata`."
    ),
)

parsed = _parse_args(raw)

st.caption(
    f"`{len(parsed)}` flag(s) parsed. "
    "`--models-directory /vault/comfyui` is hardcoded before your flags; "
    "appending `--models-directory <other>` here will override it (last-wins)."
)

# Surface any error from the previous Save & restart attempt, mirroring
# the pattern in ``utils/ui/_service_controls.py``.
pending_error = st.session_state.pop(_ERROR_KEY, None)
if pending_error:
    st.error(pending_error)


def _persist() -> None:
    """Write the parsed flags to the sidecar and update in-memory state."""
    svc.set_extra_args(parsed)
    if parsed:
        worker.write_service_overrides(SERVICE_NAME, {"extra_args": parsed})
    else:
        # Empty list reverts to the YAML default; pop the key so the
        # next construction reads the default cleanly.
        existing = dict(worker.read_service_overrides(SERVICE_NAME))
        existing.pop("extra_args", None)
        worker.write_service_overrides(SERVICE_NAME, existing)


cols = st.columns(2)
with cols[0]:
    if st.button("Save", key="flags-save", use_container_width=True):
        _persist()
        st.success(f"Saved {len(parsed)} flag(s). Restart ComfyUI to apply.")
        st.rerun()

with cols[1]:
    save_restart_disabled = not svc.is_running()
    save_restart_help = (
        None
        if svc.is_running()
        else "Start ComfyUI first, then Save & restart will stop + start it."
    )
    if st.button(
        "Save & restart",
        key="flags-save-restart",
        disabled=save_restart_disabled,
        help=save_restart_help,
        use_container_width=True,
    ):
        _persist()
        worker.stop_service(SERVICE_NAME)
        result = worker.start_service(SERVICE_NAME)
        if not result.ok:
            st.session_state[_ERROR_KEY] = (
                f"Flags saved, but restart failed: {result.message}. "
                "The container is stopped; check Console for details."
            )
        else:
            st.success(f"Saved {len(parsed)} flag(s) and restarted ComfyUI.")
        st.rerun()
