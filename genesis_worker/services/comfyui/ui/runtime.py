"""Runtime tweaks for the ComfyUI container (ADR-041).

Two editors, one Save, one Save & restart:

- **CLI flags** — appended after the bake-in ``--models-directory
  /vault/comfyui``. Each line is shell-tokenised, so flags with
  values (``--reserve-vram 10``) become two argv tokens. Persists
  to ``extra_args`` in the per-service JSON sidecar.

- **Env vars** — one ``KEY=VALUE`` per line. Matching surrounding
  quotes are stripped, unquoted spaces in the value are preserved,
  ``#`` comments and blank lines are ignored. Persists to
  ``extra_env`` in the same sidecar.

The page writes the full sidecar with both keys on save (the
Runtime page is the only writer; this is by design, not a
read-modify-write). The bake-in prefix and framework-managed
env (PUID, PGID, ROCm) live in the service and are not editable
from this page; they show up in the Status page's read-only
``Env:`` block alongside the user-supplied env.
"""

from __future__ import annotations

import streamlit as st

from genesis_worker.services.comfyui.cli_args import parse_extra_args, parse_extra_env

SERVICE_NAME = "comfyui"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title("Runtime")
st.caption(
    "CLI flags and env vars appended to the container at start. Both editors "
    "share a single Save; either set takes effect on Save & restart."
)

_ERROR_KEY = "runtime-start_error"

# Pull both fields from the sidecar so the form reflects what's
# actually persisted, not just the in-memory defaults. The Runtime
# page is the only writer, so the sidecar is the source of truth
# once the user has saved anything.
sidecar = worker.read_service_overrides(SERVICE_NAME)
args_persisted = sidecar.get("extra_args")
env_persisted = sidecar.get("extra_env")

args_default = args_persisted if args_persisted is not None else svc._options.extra_args
env_default = env_persisted if env_persisted is not None else svc._options.extra_env


def _env_to_text(env: dict[str, str]) -> str:
    """Render an env dict as a text area's ``KEY=VALUE`` per line."""
    return "\n".join(f"{k}={v}" for k, v in env.items())


# --- CLI flags editor -------------------------------------------------------
st.subheader("CLI flags")
st.caption(
    "Each line is shell-tokenised: `--reserve-vram 10` becomes two argv tokens "
    "(`--reserve-vram`, `10`). Empty lines are ignored. The bake-in "
    "`--models-directory /vault/comfyui` is prepended at the container level."
)
raw_args = st.text_area(
    "Extra flags (one per line)",
    key="runtime-flags-text-area",
    value="\n".join(args_default),
    height=200,
    label_visibility="collapsed",
)
parsed_args = parse_extra_args(raw_args)
st.caption(f"`{len(parsed_args)}` argv token(s) parsed.")

st.divider()

# --- Env vars editor --------------------------------------------------------
st.subheader("Env vars")
st.caption(
    "One `KEY=VALUE` per line. Matching quotes are stripped, unquoted spaces "
    "are preserved, `#` comments and blank lines are ignored. User-supplied "
    "env wins on key collisions with PUID/PGID/ROCm (ADR-036 env_map semantics)."
)
raw_env = st.text_area(
    "Env vars (one KEY=VALUE per line)",
    key="runtime-env-text-area",
    value=_env_to_text(env_default),
    height=200,
    label_visibility="collapsed",
)
parsed_env = parse_extra_env(raw_env)
st.caption(f"`{len(parsed_env)}` env var(s) parsed.")

# Surface any error from the previous Save & restart attempt, mirroring
# the pattern in ``utils/ui/_service_controls.py``.
pending_error = st.session_state.pop(_ERROR_KEY, None)
if pending_error:
    st.error(pending_error)


def _persist() -> dict:
    """Write the full sidecar with both fields; update in-memory state.

    The Runtime page is the sole writer, so this is a full rewrite
    (not read-modify-write) of the sidecar. Empty values mean the
    key is dropped so the YAML default re-emerges on the next
    construction: empty ``args`` drops ``extra_args`` from the
    sidecar; empty ``env`` drops ``extra_env``.
    """
    svc.set_extra_args(parsed_args)
    svc.set_extra_env(parsed_env)
    values: dict = {}
    if parsed_args:
        values["extra_args"] = parsed_args
    if parsed_env:
        values["extra_env"] = parsed_env
    worker.write_service_overrides(SERVICE_NAME, values)
    return values


cols = st.columns(2)
with cols[0]:
    if st.button("Save", key="runtime-save", use_container_width=True):
        written = _persist()
        keys = ", ".join(sorted(written)) if written else "no overrides (defaults apply)"
        st.success(f"Saved. Sidecar: {keys}.")
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
        key="runtime-save-restart",
        disabled=save_restart_disabled,
        help=save_restart_help,
        use_container_width=True,
    ):
        _persist()
        worker.stop_service(SERVICE_NAME)
        result = worker.start_service(SERVICE_NAME)
        if not result.ok:
            st.session_state[_ERROR_KEY] = (
                f"Saved, but restart failed: {result.message}. "
                "The container is stopped; check Console for details."
            )
        else:
            st.success("Saved and restarted ComfyUI with the new runtime config.")
        st.rerun()
