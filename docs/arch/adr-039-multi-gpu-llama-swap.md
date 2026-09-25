# ADR-039: Multi-GPU llama-swap — per-device selection

## Title
Multi-GPU llama-swap — per-device selection via service-level default + per-model override, env-var cmd prefix

## Context
The fleet is growing extra GPU capacity. Most nodes will still have a single GPU, but some — developer laptops with an Intel iGPU plus an NVIDIA discrete card (confirmed on `omarchy`: NVIDIA RTX 2060 Mobile + Intel iGPU), future workstations with 2× NVIDIA — will have multiple devices the operator wants to pin specific models to.

The framework already enumerates per-vendor GPU counts in `genesis_worker/utils/collectors/hardware.py` and hands the snapshot to plugins via `PluginContext.host_info` (`contracts/context.py:54`). The existing `llama_server_variant` knob (`auto / cuda / cpu / vulkan`) picks which *binary* the config generator uses, but not which *device* that binary should target.

Llama.cpp honours two env vars for device selection:

- `CUDA_VISIBLE_DEVICES=<index>` for the CUDA backend (NVIDIA).
- `GGML_VK_VISIBLE_DEVICES=<index>` for the Vulkan backend (AMD, Intel, sometimes NVIDIA via Vulkan).

Both take a zero-based device index. The indices are independent of `/sys/class/drm/card*` order — they index whatever the runtime enumerates. Tensor parallelism (`CUDA_VISIBLE_DEVICES=0,1` for "split across two cards") is explicitly out of scope; each model runs on exactly one device.

Three open questions settled during design:

1. **AMD/Intel path.** Vulkan only for now. ROCm and OpenVINO are deferred, but the design must leave room to add them without a structural change.
2. **Always emit the env var when selected.** No edge-case branching on single-GPU hosts — one rendering path is easier to reason about.
3. **Fail loud on missing binary.** When `default_gpu` is set but the matching variant binary is not installed, raise at config-regen time. Silent fallback masks misconfiguration.

## Decision

### 1. `Hardware` contract gains per-device enumeration

`genesis_worker/contracts/host.py` adds a new frozen dataclass:

```python
@dataclass(frozen=True)
class GpuDevice:
    vendor: Literal["nvidia", "amd", "intel"]
    index: int
    label: str
```

`Hardware` gains `devices: tuple[GpuDevice, ...] = ()` alongside the existing per-vendor booleans and counts. The change is additive — no existing field changes shape or semantics. The high-level booleans stay because they are cheap O(1) checks consumed in many places; `devices` is the structured list consumed by selection UI.

The collector populates `devices` from one source per vendor:

- **NVIDIA.** `nvidia-smi --query-gpu=index,name --format=csv,noheader,nounits`. Each row becomes one `GpuDevice(vendor="nvidia", index=<int>, label=<name>)`. This folds the existing count-only `_nvidia_smi_count()` probe into one subprocess call.
- **AMD / Intel.** Derived from the PCI enumeration. Each card with vendor `0x1002` (AMD) or `0x8086` (Intel) gets a sequential index per vendor and a generic label `"Vulkan device {i}"`. We do not probe Vulkan enumeration (`vulkaninfo`) — it is not universally installed (the `omarchy` host has no `vulkaninfo`), and adding the dependency is out of scope.

`vendor_summary()` is unchanged. The collector's existing tests continue to pass — the new field is purely additive.

### 2. Service-level `default_gpu` option

`LlamaSwapOptions` gains `default_gpu: GpuDevice | None = None`. Persistence follows the existing `LlamaSwapOptions` pattern — flat key under `Settings.services.llama_swap`, so `GENESIS_SERVICES__LLAMA_SWAP__DEFAULT_GPU` works.

`LlamaSwapService._auto_resolve()` becomes:

```python
def _auto_resolve(self) -> str | None:
    requested = self._options.default_gpu
    if requested is None:
        # Today's cascade: NVIDIA → cuda, anything → vulkan, else cpu.
        if self._ctx.host_info.hardware.nvidia:
            binary = self._variant_binary("llama-server-cuda")
            if binary is not None:
                return binary
        binary = self._variant_binary("llama-server-vulkan")
        if binary is not None:
            return binary
        return self._variant_binary("llama-server-cpu")
    # default_gpu is set: honour it, fail loud if the binary is missing.
    variant = _variant_for_vendor(requested.vendor)  # "cuda" | "vulkan" | None
    if variant is None:
        raise RuntimeError(
            f"no framework-managed variant for GPU vendor {requested.vendor!r}"
        )
    binary = self._variant_binary(f"llama-server-{variant}")
    if binary is None:
        raise RuntimeError(
            f"default_gpu is {requested.label!r} (vendor={requested.vendor!r}) but "
            f"llama-server-{variant} is not installed — install it via the Binaries page "
            f"or clear default_gpu in the service config"
        )
    return binary
```

`LlamaSwapService.set_default_gpu(gpu: GpuDevice | None)` mirrors `set_llama_server_variant` for the UI write path. When `gpu is not None`, it must appear in `ctx.host_info.hardware.devices` — the validation prevents the UI from persisting a stale device that no longer exists on this host.

### 3. Per-model override `gpu`

`Recipe` gains `gpu: GpuDevice | None = None`. `EvaluatedConfig` gains the same field. The cascade in `evaluate_recipe` is `override > recipe > default_recipe > None` — same shape as `parallel`, `ctx_min`, `ctx_size`, etc. Provenance is tracked in the existing `provenance: dict[str, FieldSource]` under the key `"gpu"`.

Per-model overrides continue to flow through `OverridesStore` (unstructured dict). No schema change to the storage format.

### 4. Cmd emission rule with the `_ENV_VAR_FOR` extensibility seam

`BuildOptions` gains `binary_variant: str | None = None`. The service populates it from `_options.llama_server_variant` (or `None` when the legacy fallback path is active). It threads through `evaluate_recipe` → `cmd_from_evaluated_dict`.

`cmd_from_evaluated_dict` prepends an env-var prefix to the rendered cmd when all three conditions hold:

1. `gpu` is non-`None`.
2. `binary_variant` is non-`None`.
3. `(binary_variant, gpu.vendor)` is in `_ENV_VAR_FOR`.

```python
_ENV_VAR_FOR: dict[tuple[str, str], str] = {
    ("cuda", "nvidia"):   "CUDA_VISIBLE_DEVICES",
    ("vulkan", "amd"):    "GGML_VK_VISIBLE_DEVICES",
    ("vulkan", "intel"):  "GGML_VK_VISIBLE_DEVICES",
    # Future extensibility hooks (do not implement now):
    # ("rocm", "amd"):      "HIP_VISIBLE_DEVICES",
    # ("openvino", "intel"): "ONEAPI_DEVICE_SELECTOR",
}

def _env_prefix(binary_variant: str | None, gpu: GpuDevice | None) -> str:
    if binary_variant is None or gpu is None:
        return ""
    env_var = _ENV_VAR_FOR.get((binary_variant, gpu.vendor))
    if env_var is None:
        return ""
    return f"{env_var}={gpu.index} "
```

The cmd becomes `<env_prefix><binary> \`. Single-GPU hosts that leave `default_gpu` unset get a byte-identical cmd to today — existing golden-file tests in `test_generate_config.py` continue to pass without modification. When `binary_variant is None` (legacy fallback path), no prefix is emitted regardless of `gpu` — we do not know what backend the legacy binary uses.

The `_ENV_VAR_FOR` dict is the single seam for future backends. Adding ROCm = one new tuple; adding OpenVINO = one new tuple. No conditional logic in the cmd renderer.

### 5. UI surfaces

**Service-level default** (`services/llama_swap/ui/status.py`) gains a "Default GPU" container mirroring the existing "Variant" container:

- Dropdown of `ctx.host_info.hardware.devices`, prefixed with "(use cascade)".
- On change, calls `svc.set_default_gpu(value)` then `worker.regenerate_service_config(SERVICE_NAME)`.

**Per-model override** (`services/llama_swap/ui/config_editor.py`) gains a "GPU" dropdown in `_render_override_form`:

- Dropdown of all `ctx.host_info.hardware.devices`, prefixed with "(use service default)" (= `None`).
- Save path unchanged (`svc.save_overrides_for_entry(entry_id, new_overrides)`).

The service exposes `host_info` as a read-through property so the UI does not reach into the private context.

## Status
Accepted.

## Consequences

Positive:

- Single-GPU hosts: zero behaviour change. No new env var, no new option, no new UI field shown unless the operator opens the dropdown.
- Multi-GPU hosts: operators can pin models to specific devices from the same config surface they already use for ctx size and parallel slots.
- The `_ENV_VAR_FOR` dict is the single seam for future backend additions. ROCm → one tuple. OpenVINO → one tuple. No new conditional logic.
- Vendor classification stays in one place (`contracts/host.py`). The collector never duplicates the vendor-id-to-name mapping.

Negative:

- The dropdown shows "Vulkan device 0" for AMD/Intel devices, which is opaque to operators. Accepted — `vulkaninfo` is not universally installed, and shipping a Vulkan probe is a dependency we do not want today. When llama.cpp reports "no Vulkan device N" the operator knows to pick a different index.
- Per-model `gpu` can be set even when the resolved binary does not match the vendor (the override UI does not enforce compatibility — the cmd layer silently no-ops on an unknown `(variant, vendor)` pair). Documented; not a footgun because the cmd just runs without the env var.
- `default_gpu` is a *named device position*, not a stable identity. If the operator swaps the GPU in slot 0 for a different model, the saved `default_gpu` still points to index 0 — possibly wrong device. We document this; we do not persist UUIDs (no portable way to obtain them across vendors).

Neutral:

- The collector runs `nvidia-smi` once (was once for `-L`, still once — folded into `--query-gpu`). No additional subprocess calls in the hot path.
- `Recipe.gpu` is a new optional field. Bundled recipes do not set it; default is `None`. User recipes can set it.
- `Hardware.devices` ordering follows collector enumeration order, which is whatever the runtime returns. Stable across calls (`lru_cache`), not stable across reboots or device hot-plug. Documented as expected.

## Plan

[`docs/arch/plans/plan-039-multi-gpu-llama-swap.md`](plans/plan-039-multi-gpu-llama-swap.md) — file-by-file execution in four independently testable phases.
