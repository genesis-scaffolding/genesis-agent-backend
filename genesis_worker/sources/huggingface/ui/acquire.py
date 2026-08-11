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
    with st.form("select-files"):
        main_index: int | None = None
        aux_indexes: list[int] = []
        for i, group in enumerate(step.file_groups):
            options = [f.filename for f in group.files]
            chosen = st.selectbox(group.label, options, key=f"fg-{group.label}-{i}")
            idx = next(j for j, f in enumerate(group.files) if f.filename == chosen)
            if group.role == "main":
                main_index = idx
            else:
                aux_indexes.append(idx)
        if st.form_submit_button("Continue"):
            worker.submit_acquire(
                session,
                AcquireChoice(main_index=main_index, aux_indexes=aux_indexes or None),
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