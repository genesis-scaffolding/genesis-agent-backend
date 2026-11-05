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

from genesis_worker.contracts.host import ComputeDevice, CpuDevice, GpuDevice
from genesis_worker.services.llama_swap.engines import Engine
from genesis_worker.services.llama_swap.generate_config import EvaluatedConfig, FieldSource
from genesis_worker.services.llama_swap.radiance import container_model_path

SERVICE_NAME = "llama_swap"

worker = st.session_state["worker"]
svc = worker.service(SERVICE_NAME)


def _device_choice_label(device: ComputeDevice) -> str:
    if isinstance(device, CpuDevice):
        return "CPU (no GPU)"
    if isinstance(device, GpuDevice):
        return f"{device.label}  ({device.vendor}, idx {device.index})"
    raise TypeError(f"unknown device type: {type(device)}")


def _coerce_device(raw: Any) -> ComputeDevice | None:
    """Hydrate an overrides-style dict into a ComputeDevice, or return None.

    Discriminator: presence of ``vendor`` → GpuDevice; otherwise
    CpuDevice (the empty-dict case from yaml roundtrip).
    """
    if raw is None:
        return None
    if isinstance(raw, ComputeDevice):
        return raw
    if isinstance(raw, dict):
        if "vendor" in raw:
            try:
                return GpuDevice(**raw)
            except (TypeError, ValueError):
                return None
        return CpuDevice()
    return None


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
    if cfg.engine == Engine.RADIANCE:
        _render_radiance_effective(cfg)
        return

    rows: list[tuple[str, str, str]] = []

    rows.append(("Binary", cfg.binary, _badge(cfg.provenance["binary"])))
    if cfg.kv_cache:
        rows.append(("KV cache", cfg.kv_cache, _badge(cfg.provenance["kv_cache"])))
    if cfg.parallel is not None:
        rows.append(("Parallel", str(cfg.parallel), _badge(cfg.provenance["parallel"])))
    if cfg.ctx_min is not None:
        rows.append(("Fit context", str(cfg.ctx_min), _badge(cfg.provenance["ctx_min"])))
    if cfg.ctx_size is not None:
        rows.append(("Context size", str(cfg.ctx_size), _badge(cfg.provenance["ctx_size"])))
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
    if cfg.device is not None:
        rows.append(
            (
                "Device",
                _device_choice_label(cfg.device),
                _badge(cfg.provenance["device"]),
            )
        )
    if cfg.env:
        rows.append(
            ("Env (per-model)", "\n".join(cfg.env), ""),
        )

    rows.append(("Hardcoded flags (always)", " ".join(cfg.hardcoded_flags), ""))

    st.dataframe(
        [{"Field": k, "Value": v, "Source": badge} for k, v, badge in rows],
        hide_index=True,
        width="stretch",
    )


def _render_radiance_effective(cfg: EvaluatedConfig) -> None:
    settings = cfg.radiance
    if settings is None:
        st.error("Radiance settings are unavailable.")
        return

    def source(key: str) -> str:
        return _badge(cfg.provenance.get(key, FieldSource.COMPUTED))

    group_gids = ", ".join(f"{name}={gid}" for name, gid in settings.group_gids)
    rows: list[tuple[str, str, str]] = [
        ("Engine", "radiance (container)", ""),
        ("Image", settings.image_ref, ""),
        ("Container model path", container_model_path(cfg.files.filename), ""),
        ("GPU index", str(settings.gpu_index), ""),
        ("Group GIDs", group_gids or "none", ""),
        ("tp", str(settings.tp), source("tp")),
        ("max_model_len", str(settings.max_model_len), source("max_model_len")),
        ("max_num_seqs", str(settings.max_num_seqs), source("max_num_seqs")),
        ("kv_cache_dtype", settings.kv_cache_dtype, source("kv_cache_dtype")),
        ("gpu_headroom_mib", str(settings.gpu_headroom_mib), source("gpu_headroom_mib")),
        ("ttl", str(settings.ttl_s), ""),
        ("unloadTimeout", str(settings.unload_timeout_s), ""),
        ("checkEndpoint", settings.check_endpoint, ""),
    ]
    if settings.extra_flags:
        rows.append(("Extra flags", " ".join(settings.extra_flags), source("extra_flags")))

    st.dataframe(
        [{"Field": k, "Value": v, "Source": badge} for k, v, badge in rows],
        hide_index=True,
        width="stretch",
    )


def _render_radiance_override_form(
    svc: Any,
    entry_id: str,
    cfg: EvaluatedConfig,
    current_overrides: dict,
) -> None:
    settings = cfg.radiance
    if settings is None:
        st.error("Radiance settings are unavailable.")
        return

    raw_current = current_overrides.get("radiance")
    current_block = raw_current if isinstance(raw_current, dict) else {}
    new_block: dict[str, Any] = {}

    def numeric_override(
        label: str,
        key: str,
        effective: int,
        min_value: int,
        *,
        widget_key: str,
    ) -> None:
        raw_current = current_block.get(key)
        initial: int | None = None
        if raw_current is not None:
            try:
                initial = int(raw_current)
            except (TypeError, ValueError):
                st.error(f"{label} must be an integer")
        value = st.number_input(
            f"{label} (effective: {effective})",
            min_value=min_value,
            value=initial,
            step=1,
            key=f"ov-{entry_id}-{widget_key}",
        )
        if value is None:
            return
        value = int(value)
        if key in current_block or value != effective:
            new_block[key] = value

    with st.expander("Override"):
        st.caption(
            "Edit a radiance field to override the recipe value. "
            "Clearing a field reverts to the recipe default."
        )
        numeric_override(
            "Max model len",
            "max_model_len",
            settings.max_model_len,
            0,
            widget_key="radiance-max-model-len",
        )
        numeric_override(
            "Max num seqs",
            "max_num_seqs",
            settings.max_num_seqs,
            1,
            widget_key="radiance-max-num-seqs",
        )
        numeric_override("TP", "tp", settings.tp, 1, widget_key="radiance-tp")

        dtype_options = ["(use cascade)", "bf16", "fp8", "fp8_v4", "k6_v4"]
        current_dtype = str(current_block.get("kv_cache_dtype", settings.kv_cache_dtype))
        if current_dtype not in dtype_options:
            dtype_options.append(current_dtype)
        dtype_choice = st.selectbox(
            "KV cache dtype",
            dtype_options,
            index=dtype_options.index(current_dtype),
            key=f"ov-{entry_id}-radiance-kv",
            help=("fp8_v4 and k6_v4 are accepted only by Qwen3.8-27B and Qwen3.8-Flash-Next."),
        )
        if dtype_choice != "(use cascade)" and (
            "kv_cache_dtype" in current_block or dtype_choice != settings.kv_cache_dtype
        ):
            new_block["kv_cache_dtype"] = dtype_choice

        numeric_override(
            "GPU headroom MiB",
            "gpu_headroom_mib",
            settings.gpu_headroom_mib,
            0,
            widget_key="radiance-gpu-headroom",
        )

        raw_extra = current_block.get("extra_flags")
        current_extra = raw_extra if isinstance(raw_extra, list) else list(settings.extra_flags)
        extra_value = st.text_area(
            "Extra radiance flags (one per line)",
            value="\n".join(str(flag) for flag in current_extra),
            key=f"ov-{entry_id}-radiance-ef",
            height=100,
        )
        extra_flags = [line.strip() for line in (extra_value or "").splitlines() if line.strip()]
        if extra_flags and (
            "extra_flags" in current_block or extra_flags != list(settings.extra_flags)
        ):
            new_block["extra_flags"] = extra_flags

        cols = st.columns([1, 1])
        with cols[0]:
            if st.button("Save override", key=f"ov-{entry_id}-save"):
                updated = dict(current_overrides)
                updated.pop("radiance", None)
                if new_block:
                    updated["radiance"] = new_block
                svc.save_overrides_for_entry(entry_id, updated)
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


def _render_override_form(
    svc: Any, entry_id: str, cfg: EvaluatedConfig, current_overrides: dict
) -> None:
    if cfg.engine == Engine.RADIANCE:
        _render_radiance_override_form(svc, entry_id, cfg, current_overrides)
        return

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

        # --- Device ---
        # Per-model compute device pin (ADR-039 §3). ``None`` = inherit
        # from the service-level ``effective_default_device`` (the
        # persisted value or the smart auto-pick). The dropdown shows
        # detected GPUs *and* ``CpuDevice`` (synthesised, since CPU
        # isn't a "detected device" but is always a valid choice).
        # "(use service default)" is the explicit None option so
        # operators can revert to the head honcho; the current
        # selection previews what that resolves to so the operator
        # sees the effective pick even when it is implicit.
        gpus = svc.host_info.hardware.devices
        # Always include CPU as a selectable entry, even when GPUs are
        # present. CPU isn't in ``Hardware.devices`` so we inject it
        # here; the user can pick it as an explicit override of the
        # GPU smart pick (or as the only option on a device-less host).
        dropdown_choices: tuple = (*gpus, CpuDevice())
        device_labels = ["(use service default)"] + [
            _device_choice_label(d) for d in dropdown_choices
        ]
        # Read the canonical ``device`` key first, fall back to the
        # legacy ``gpu`` alias for backward compat with pre-refactor
        # overrides.yaml.
        current_device = _coerce_device(
            current_overrides.get("device") or current_overrides.get("gpu")
        )
        if current_device is None:
            current_device_idx = 0
            effective = svc.effective_default_device
            st.caption(f"Service default resolves to: {_device_choice_label(effective)}")
        else:
            current_label = _device_choice_label(current_device)
            current_device_idx = (
                device_labels.index(current_label) if current_label in device_labels else 0
            )
        device_choice = st.selectbox(
            "Device",
            device_labels,
            index=current_device_idx,
            key=f"ov-{entry_id}-device",
        )
        if device_choice != "(use service default)":
            chosen_device = next(
                (d for d in dropdown_choices if _device_choice_label(d) == device_choice),
                None,
            )
            if chosen_device is not None:
                new_overrides["device"] = chosen_device

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

        # --- Context Size (cap) ---
        cs_val = st.text_input(
            "Context size (cap)",
            value=str(
                current_overrides.get("ctx_size", cfg.ctx_size)
                if current_overrides.get("ctx_size") is not None or cfg.ctx_size is not None
                else ""
            ),
            key=f"ov-{entry_id}-ctx-size",
        )
        if (cs_val or "").strip():
            try:
                new_overrides["ctx_size"] = int(cs_val)
            except ValueError:
                st.error("Context size must be an integer")

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
if svc.radiance_image_ref() is None:
    st.info(
        "Radiance models are disabled. Enable the radiance installable on the "
        "Binaries page to generate radiance entries."
    )
if st.button("↻ Regenerate config", key=regen_key, disabled=not ready):
    ok = worker.regenerate_service_config(SERVICE_NAME)
    st.success("Regenerated") if ok else st.info("Already up to date")
    st.rerun()

catalog = worker.catalog()
report = svc.evaluate_model_config_report(catalog)
configs = report.configs
overrides_store = svc.list_overrides()

for error in report.errors:
    st.error(f"Skipped `{error.entry_id}` ({error.name}): invalid override — {error.message}")

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
        if cfg.engine == Engine.RADIANCE:
            target = "radiance (container)"
        else:
            target = Path(cfg.binary).name or "(no binary)"
        label = (
            f"{entry_id}  →  {target}"
            if cfg.matched_recipe is None
            else f"{entry_id}  →  {target}   (recipe: {cfg.matched_recipe})"
        )
        with st.expander(label):
            st.subheader(cfg.name)

            st.markdown("**Files** _(auto-detected)_")
            if cfg.files.main:
                if cfg.engine == Engine.RADIANCE:
                    st.markdown(f"- host model:      `{cfg.files.main}`")
                    st.markdown(f"- container model: `{container_model_path(cfg.files.filename)}`")
                else:
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

            with st.expander("Raw cmd + env"):
                st.code(cfg.cmd)
                if cfg.env:
                    st.markdown("**Env (injected before exec):**")
                    for entry in cfg.env:
                        st.code(entry, language="bash")
