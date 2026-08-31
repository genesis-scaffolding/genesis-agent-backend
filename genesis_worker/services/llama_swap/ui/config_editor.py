"""Inspect the live config + per-model overrides, all from the data model.

The UI never parses cmd back out. It calls
:func:`evaluate_model_config` (recipe + overrides + files → structured
fields) and renders directly from those.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from genesis_worker.services.llama_swap.generate_config import EvaluatedConfig, FieldSource

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)


# ---------------------------------------------------------------------------
# Helpers (defined first so the page loop can call them by name)
# ---------------------------------------------------------------------------


def _badge(source: FieldSource) -> str:
    return {
        FieldSource.OVERRIDE: "override",
        FieldSource.RECIPE: "recipe",
        FieldSource.DEFAULT: "default",
        FieldSource.COMPUTED: "computed",
    }.get(source, "")


def _render_effective(cfg: EvaluatedConfig) -> None:
    rows: list[tuple[str, str, str]] = []

    rows.append(("Binary", cfg.binary, _badge(cfg.provenance["binary"])))
    if cfg.kv_cache:
        rows.append(("KV cache", cfg.kv_cache, _badge(cfg.provenance["kv_cache"])))
    if cfg.parallel is not None:
        rows.append(("Parallel", str(cfg.parallel), _badge(cfg.provenance["parallel"])))
    if cfg.ctx_min is not None:
        rows.append(("Fit context", str(cfg.ctx_min), _badge(cfg.provenance["ctx_min"])))
    if cfg.mmproj_offload is not None:
        rows.append(
            (
                "mmproj offload",
                str(cfg.mmproj_offload),
                _badge(cfg.provenance["mmproj_offload"]),
            )
        )
    if cfg.spec:
        rows.append(("Spec", str(cfg.spec), _badge(cfg.provenance["spec"])))
    if cfg.reasoning_budget is not None:
        rows.append(
            (
                "Reasoning budget",
                str(cfg.reasoning_budget),
                _badge(cfg.provenance["reasoning_budget"]),
            )
        )
    if cfg.reasoning_budget_message:
        rows.append(
            (
                "Reasoning budget msg",
                cfg.reasoning_budget_message,
                _badge(cfg.provenance["reasoning_budget_message"]),
            )
        )
    if cfg.chat_template_file:
        rows.append(
            (
                "Chat template",
                cfg.chat_template_file,
                _badge(cfg.provenance["chat_template_file"]),
            )
        )
    if cfg.sampling:
        rows.append(("Sampling", str(cfg.sampling), _badge(cfg.provenance["sampling"])))
    if cfg.chat_template_kwargs:
        rows.append(
            (
                "Chat template kwargs",
                str(cfg.chat_template_kwargs),
                _badge(cfg.provenance["chat_template_kwargs"]),
            )
        )
    if cfg.extra_flags:
        rows.append(
            (
                "Extra flags",
                " ".join(cfg.extra_flags),
                _badge(cfg.provenance["extra_flags"]),
            )
        )

    rows.append(("Hardcoded flags (always)", " ".join(cfg.hardcoded_flags), ""))

    st.dataframe(
        [{"Field": k, "Value": v, "Source": badge} for k, v, badge in rows],
        hide_index=True,
        width="stretch",
    )


def _render_override_form(
    svc: Any, entry_id: str, cfg: EvaluatedConfig, current_overrides: dict
) -> None:
    with st.expander("Override"):
        st.caption(
            "Edit a field to override the recipe value. "
            "Clearing a field reverts to the recipe default."
        )

        new_overrides: dict = {}

        # --- Binary ---
        variant_options: list[str] = ["(use cascade)", "Custom path…"]
        variant_values: list[str | None] = [None, "__custom__"]
        for installable in svc.installs():
            if not installable.name.startswith("llama-server-"):
                continue
            bp = installable.binary_path()
            if bp is None:
                continue
            variant_options.append(f"{installable.name} ({bp})")
            variant_values.append(str(bp))

        current_binary = current_overrides.get("binary", cfg.binary)
        if current_binary is None:
            current_idx = 0
        elif current_binary in variant_values:
            current_idx = variant_values.index(current_binary)
        else:
            current_idx = 1  # Custom path

        choice = st.selectbox(
            "Binary",
            variant_options,
            index=current_idx,
            key=f"ov-{entry_id}-binary",
        )
        if choice != "(use cascade)":
            if choice == "Custom path…":
                custom_default = current_binary if current_binary not in variant_values else ""
                val = st.text_input(
                    "Custom binary path",
                    value=custom_default,
                    key=f"ov-{entry_id}-binary-custom",
                )
                if (val or "").strip():
                    new_overrides["binary"] = val
            else:
                idx = variant_options.index(choice)
                new_overrides["binary"] = variant_values[idx]

        # --- KV Cache ---
        kv_val = st.text_input(
            "KV cache (q4_0 / q8_0)",
            value=str(current_overrides.get("kv_cache", cfg.kv_cache or "")),
            key=f"ov-{entry_id}-kv",
        )
        if (kv_val or "").strip():
            new_overrides["kv_cache"] = kv_val

        # --- Parallel ---
        par_val = st.text_input(
            "Parallel",
            value=str(
                current_overrides.get("parallel", cfg.parallel)
                if current_overrides.get("parallel") is not None or cfg.parallel is not None
                else ""
            ),
            key=f"ov-{entry_id}-parallel",
        )
        if (par_val or "").strip():
            try:
                new_overrides["parallel"] = int(par_val)
            except ValueError:
                st.error("Parallel must be an integer")

        # --- Fit Context ---
        ctx_val = st.text_input(
            "Fit context",
            value=str(
                current_overrides.get("ctx_min", cfg.ctx_min)
                if current_overrides.get("ctx_min") is not None or cfg.ctx_min is not None
                else ""
            ),
            key=f"ov-{entry_id}-ctx",
        )
        if (ctx_val or "").strip():
            try:
                new_overrides["ctx_min"] = int(ctx_val)
            except ValueError:
                st.error("Fit context must be an integer")

        # --- Reasoning Budget ---
        rb_val = st.text_input(
            "Reasoning budget",
            value=str(
                current_overrides.get("reasoning_budget", cfg.reasoning_budget)
                if current_overrides.get("reasoning_budget") is not None
                or cfg.reasoning_budget is not None
                else ""
            ),
            key=f"ov-{entry_id}-rb",
        )
        if (rb_val or "").strip():
            try:
                new_overrides["reasoning_budget"] = int(rb_val)
            except ValueError:
                st.error("Reasoning budget must be an integer")

        # --- Reasoning Budget Message ---
        rbm_val = st.text_input(
            "Reasoning budget message",
            value=current_overrides.get(
                "reasoning_budget_message", cfg.reasoning_budget_message or ""
            ),
            key=f"ov-{entry_id}-rbm",
        )
        if (rbm_val or "").strip():
            new_overrides["reasoning_budget_message"] = rbm_val

        # --- Chat Template File ---
        ctf_val = st.text_input(
            "Chat template file",
            value=current_overrides.get("chat_template_file", cfg.chat_template_file or ""),
            key=f"ov-{entry_id}-ctf",
        )
        if (ctf_val or "").strip():
            new_overrides["chat_template_file"] = ctf_val

        # --- mmproj offload ---
        mm_ov = st.checkbox(
            "Override mmproj offload",
            value="mmproj_offload" in current_overrides,
            key=f"ov-{entry_id}-mmproj-ov",
        )
        if mm_ov:
            new_overrides["mmproj_offload"] = st.checkbox(
                "mmproj offload (True = use --no-mmproj-offload)",
                value=bool(current_overrides.get("mmproj_offload", cfg.mmproj_offload)),
                key=f"ov-{entry_id}-mmproj",
            )

        # --- Sampling ---
        sampling_val = st.text_area(
            "Sampling (JSON)",
            value=json.dumps(current_overrides.get("sampling", cfg.sampling) or {}, indent=2),
            key=f"ov-{entry_id}-sampling",
            height=120,
        )
        if (sampling_val or "").strip():
            try:
                new_overrides["sampling"] = json.loads(sampling_val)
            except json.JSONDecodeError as exc:
                st.error(f"Sampling JSON invalid: {exc}")

        # --- Chat Template Kwargs ---
        ctk_val = st.text_area(
            "Chat template kwargs (JSON)",
            value=json.dumps(
                current_overrides.get("chat_template_kwargs", cfg.chat_template_kwargs) or {},
                indent=2,
            ),
            key=f"ov-{entry_id}-ctk",
            height=100,
        )
        if (ctk_val or "").strip():
            try:
                new_overrides["chat_template_kwargs"] = json.loads(ctk_val)
            except json.JSONDecodeError as exc:
                st.error(f"Chat template kwargs JSON invalid: {exc}")

        # --- Extra flags ---
        ef_val = st.text_area(
            "Extra llama.cpp flags (one per line)",
            value="\n".join(current_overrides.get("extra_flags", cfg.extra_flags or [])),
            key=f"ov-{entry_id}-ef",
            height=100,
        )
        if (ef_val or "").strip():
            new_overrides["extra_flags"] = [ln.strip() for ln in ef_val.splitlines() if ln.strip()]

        # --- Spec ---
        spec_val = st.text_area(
            "Spec (JSON)",
            value=json.dumps(current_overrides.get("spec", cfg.spec) or {}, indent=2),
            key=f"ov-{entry_id}-spec",
            height=100,
        )
        if (spec_val or "").strip():
            try:
                new_overrides["spec"] = json.loads(spec_val)
            except json.JSONDecodeError as exc:
                st.error(f"Spec JSON invalid: {exc}")

        cols = st.columns([1, 1])
        with cols[0]:
            if st.button("Save override", key=f"ov-{entry_id}-save"):
                svc.save_overrides_for_entry(entry_id, new_overrides)
                ok = worker.regenerate_service_config(SERVICE_NAME)
                if ok:
                    st.success("Saved + regenerated.")
                else:
                    st.info("Saved (config already up to date).")
                st.rerun()
        with cols[1]:
            if st.button("Clear override", key=f"ov-{entry_id}-clear"):
                svc.save_overrides_for_entry(entry_id, {})
                worker.regenerate_service_config(SERVICE_NAME)
                st.success("Override cleared.")
                st.rerun()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("Config editor")
st.caption("Inspect the live config + override individual model fields.")
st.markdown(f"`{svc.config_path}`")

regen_key = "regen-config-editor"
ready = svc.is_ready_to_serve()
if not ready:
    st.warning(
        "No llama-server binary is available. Install a variant via the "
        "Binaries page or set the legacy fallback to a valid path."
    )
if st.button("↻ Regenerate config", key=regen_key, disabled=not ready):
    ok = worker.regenerate_service_config(SERVICE_NAME)
    st.success("Regenerated") if ok else st.info("Already up to date")
    st.rerun()

catalog = worker.catalog()
configs = svc.evaluate_model_config(catalog)
overrides_store = svc.list_overrides()

if not configs:
    st.error(
        "No models evaluated. The catalog + recipes produced no entries. "
        "Rescan the catalog from the dashboard."
    )
    st.stop()

last_gen = svc.last_generated_at()
if last_gen is None or last_gen != catalog.generated_at:
    st.warning(
        f"Config is stale (last generated `{last_gen or 'never'}`). "
        "Regenerate to pick up new models."
    )

with st.container(border=True):
    st.subheader("Models")
    for entry_id, cfg in configs.items():
        binary_name = Path(cfg.binary).name or "(no binary)"
        label = (
            f"{entry_id}  →  {binary_name}"
            if cfg.matched_recipe is None
            else f"{entry_id}  →  {binary_name}   (recipe: {cfg.matched_recipe})"
        )
        with st.expander(label):
            st.subheader(cfg.name)

            st.markdown("**Files** _(auto-detected)_")
            if cfg.files.main:
                st.markdown(f"- model:    `{cfg.files.main}`")
            if cfg.files.mmproj:
                st.markdown(f"- mmproj:   `{cfg.files.mmproj}`")
            if cfg.files.draft:
                st.markdown(f"- draft:    `{cfg.files.draft}`")
            if cfg.files.weight_bytes:
                st.caption(f"weight: {cfg.files.weight_bytes / 1e9:.2f} GB")

            st.divider()

            st.markdown("**Effective configuration**")
            _render_effective(cfg)

            st.divider()

            _render_override_form(svc, entry_id, cfg, overrides_store.get(entry_id, {}))

            st.divider()

            with st.expander("Raw cmd"):
                st.code(cfg.cmd)
