"""Inspect the live config.yaml used by llama-swap. Per-model overrides ship next."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)

st.title("Config editor")
st.caption("Inspect the live config.yaml. Per-model overrides coming soon.")

catalog = worker.catalog()
config_models = svc.read_config_models()

# --- Missing config -------------------------------------------------------
if not config_models:
    st.error(
        f"No config.yaml at `{svc.config_path}`. "
        "Regenerate from the catalog + recipes to populate it."
    )
    if st.button("Regenerate config", key="regen-missing"):
        ok = worker.regenerate_service_config(SERVICE_NAME)
        if ok:
            st.success("regenerated")
        else:
            st.info("already up to date")
        st.rerun()
    st.stop()

# --- Stale indicator ------------------------------------------------------
last_gen = svc.last_generated_at()
if last_gen is None or last_gen != catalog.generated_at:
    st.warning(
        f"Config is stale (last generated `{last_gen or 'never'}`). "
        "Regenerate to pick up new models."
    )

# --- Per-model expanders --------------------------------------------------
# Compact list shows the binary basename next to each entry_id, so the user
# can spot the odd one out (e.g. a model pinned to a custom llama.cpp
# build) without expanding anything.
for entry_id, entry in config_models.items():
    binary_name = Path(entry.binary).name or "(no binary)"
    with st.expander(f"{entry_id}  →  {binary_name}"):
        st.subheader(entry.name)

        st.markdown("**Binary**")
        st.code(entry.binary)

        st.markdown("**Flags**")
        if entry.flags:
            st.dataframe(
                [{"Flag": flag, "Value": str(value)} for flag, value in entry.flags],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("No flags.")

        with st.expander("Raw cmd"):
            st.code(entry.cmd)