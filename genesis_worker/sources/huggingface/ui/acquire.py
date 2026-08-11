"""Acquire landing page for the HuggingFace source."""

from __future__ import annotations

import time

import streamlit as st

from genesis_worker.contracts import AcquireChoice

worker = st.session_state["worker"]
sid_key = "acquire_session_huggingface"
session = st.session_state.get(sid_key)

st.header("Acquire from HuggingFace")

if session is None:
    with st.form("hf-acquire-start"):
        repo_id = st.text_input("Repo (org/name)", placeholder="unsloth/Qwen3.5-9B-MTP-GGUF")
        if st.form_submit_button("Start") and repo_id:
            session = worker.start_acquire("huggingface", repo_id)
            st.session_state[sid_key] = session
            st.rerun()
    st.stop()

step = worker.acquire_step(session)
st.subheader(step.title)

if step.kind == "select_files" and step.file_groups:
    # Each entry in step.file_groups is an AcquireFileGroup:
    # one selectable model (with shards as `paths`). Indices submitted to
    # AcquireChoice are 1-based into step.file_groups.
    groups = step.file_groups
    main_groups = [(i, g) for i, g in enumerate(groups, start=1) if g.role == "main"]
    aux_groups = [(i, g) for i, g in enumerate(groups, start=1) if g.role in ("mmproj", "mtp")]

    def _label(g) -> str:
        if g.paths:
            return f"{g.label}  ({g.paths[0]})"
        return g.label

    with st.form("select-files"):
        main_choice = None
        if main_groups:
            options = [_label(g) for _, g in main_groups]
            chosen = st.selectbox("Main model", options, key="select-main")
            main_choice = main_groups[options.index(chosen)][0]
        aux_choices: list[int] = []
        for role in ("mmproj", "mtp"):
            role_groups = [(i, g) for i, g in aux_groups if g.role == role]
            if not role_groups:
                continue
            options = [_label(g) for _, g in role_groups]
            chosen = st.selectbox(
                f"{role} (optional)",
                ["(none)", *options],
                key=f"select-{role}",
            )
            if chosen != "(none)":
                aux_choices.append(role_groups[options.index(chosen)][0])
        if st.form_submit_button("Continue"):
            worker.submit_acquire(
                session,
                AcquireChoice(main_index=main_choice, aux_indexes=aux_choices or None),
            )
            st.rerun()

elif step.kind == "confirm_storage":
    total_gb = (step.total_bytes or 0) / (1024 ** 3)
    st.warning(f"Will download {total_gb:.1f} GB")
    if st.button("Confirm"):
        worker.submit_acquire(session, AcquireChoice(confirm=True))
        st.rerun()

elif step.kind == "downloading":
    if step.progress:
        ratio = step.progress.bytes_done / max(step.progress.bytes_total, 1)
        st.progress(min(ratio, 1.0))
    if step.log_tail:
        st.code("\n".join(step.log_tail[-10:]))
    if st.button("Cancel"):
        worker.cancel_acquire(session)
    time.sleep(2)
    st.rerun()

elif step.kind in ("complete", "failed", "cancelled"):
    st.write(f"Session {step.kind}")
    if st.button("Done"):
        del st.session_state[sid_key]
        st.rerun()