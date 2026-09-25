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

**`None` is the smart-auto-pick state.** When `default_gpu` is unset, the framework computes the smart default deterministically from `host_info.hardware.devices`: NVIDIA index 0 if any NVIDIA is present, else AMD index 0 if any AMD, else Intel index 0 if any Intel. The collector enumerates devices in vendor order (NVIDIA first, then AMD, then Intel), so the smart pick is reproducible across worker restarts. When `default_gpu` is set, that specific device is pinned and the smart pick is bypassed.

`LlamaSwapService.effective_default_gpu` is the single value everything downstream consumes: persisted `default_gpu` if set, else the smart pick. The UI dropdown displays real devices only — no "use cascade" / "use default" deflect — and the smart default is pre-selected on first render. The API still exposes `set_default_gpu(None)` for programmatic "revert to smart pick", but the Status page offers no affordance for it.

`LlamaSwapService._auto_resolve()` becomes:

```python
def _auto_resolve(self) -> str | None:
    requested = self.effective_default_gpu
    if requested is not None:
        # Honour the (persisted or smart-picked) device. Force the
        # matching variant, fail loud if the binary is missing.
        variant = _variant_for_vendor(requested.vendor)
        if variant is None:
            raise RuntimeError(...)
        binary = self._variant_binary(f"llama-server-{variant}")
        if binary is None:
            raise RuntimeError(...)
        return binary
    # No devices detected on this host → fall back to the variant
    # cascade without a device pin (CPU-only worker, CI, etc.).
    if self._ctx.host_info.hardware.nvidia:
        binary = self._variant_binary("llama-server-cuda")
        if binary is not None:
            return binary
    binary = self._variant_binary("llama-server-vulkan")
    if binary is not None:
        return binary
    return self._variant_binary("llama-server-cpu")
```

`LlamaSwapService.set_default_gpu(gpu: GpuDevice | None)` mirrors `set_llama_server_variant` for the UI write path. When `gpu is not None`, it must appear in `ctx.host_info.hardware.devices` — the validation prevents the UI from persisting a stale device that no longer exists on this host.

### 3. Per-model override `gpu`

`Recipe` gains `gpu: GpuDevice | None = None`. `EvaluatedConfig` gains the same field. The cascade in `evaluate_recipe` is

```
override > recipe > default_recipe > service_default_gpu > None
```

The **service-level `default_gpu` is part of the cascade**, sitting between the recipe-default and "no pin". This is what makes the service-level option the head honcho: every model that doesn't explicitly override inherits the effective service default (persisted or smart-picked). Provenance is tracked in the existing `provenance: dict[str, FieldSource]` under the key `"gpu"`.

Per-model overrides continue to flow through `OverridesStore` (unstructured dict). No schema change to the storage format.

### 4. Per-model `env` emission with the `_ENV_VAR_FOR` extensibility seam

`BuildOptions` gains `binary_variant: str | None = None`. The service populates it from `_options.llama_server_variant` (or `None` when the legacy fallback path is active). It threads through `evaluate_recipe` → `_env_for` → `EvaluatedConfig.env`.

`llama-swap` parses `cmd` as an argv array (not a shell string), and exposes a separate `models.*.env` field as a list of `NAME=value` strings injected into the child process's environment before exec (see llama-swap docs: `kb/guides/model-runtime/writing-cmd.md`). We use that field rather than an inline `KEY=VAL binary` prefix because the prefix would be parsed by `os/exec` as a literal executable name and fail with `"executable file not found in $PATH"` (the bug that prompted this amendment).

`EvaluatedConfig.env` is populated from `_env_for(binary_variant, gpu)` when all three conditions hold:

1. `gpu` is non-`None`.
2. `binary_variant` is non-`None`.
3. `(binary_variant, gpu.vendor)` is in `_ENV_VAR_FOR`.

```python
_ENV_VAR_FOR: dict[tuple[str, str], str] = {
    ("cuda", "nvidia"):   "CUDA_VISIBLE_DEVICES",
    ("vulkan", "amd"):    "GGML_VK_VISIBLE_DEVICES",
    ("vulkan", "intel"):  "GGML_VK_VISIBLE_DEVICES",
    # Future extensibility hooks (do not implement now):
    # ("rocm", "amd"):       "HIP_VISIBLE_DEVICES",
    # ("openvino", "intel"): "ONEAPI_DEVICE_SELECTOR",
}

def _env_for(binary_variant: str | None, gpu: GpuDevice | None) -> tuple[str, ...]:
    if binary_variant is None or gpu is None:
        return ()
    env_var = _ENV_VAR_FOR.get((binary_variant, gpu.vendor))
    if env_var is None:
        return ()
    return (f"{env_var}={gpu.index}",)
```

`build_entry` adds an `env` key to the per-model YAML dict only when non-empty (so single-GPU / no-pin hosts don't get an `env: []` clutter line in their `config.yaml`):

```yaml
models:
  peculiar-ragdoll-sharp-spark-x2.5-4b-gguf:
    cmd: |
      /path/to/llama-server \
        --model /path/to/model.gguf \
        ...
    env:
      - "CUDA_VISIBLE_DEVICES=0"
    proxy: "http://127.0.0.1:${PORT}"
    ttl: 0
```

The rendered `cmd` is shell-free — no inline `KEY=VAL` prefix — and stays byte-identical to today's output when no gpu is selected (existing tests in `test_generate_config.py` continue to pass without modification). When `binary_variant is None` (legacy fallback path), no env entries are emitted regardless of `gpu` — we do not know what backend the legacy binary uses.

The `_ENV_VAR_FOR` dict is the single seam for future backends. Adding ROCm = one new tuple; adding OpenVINO = one new tuple. No new conditional logic.

### 5. UI surfaces

**Service-level default** (`services/llama_swap/ui/status.py`) gains a "Default GPU" container mirroring the existing "Variant" container:

- Dropdown of `ctx.host_info.hardware.devices` (no "use cascade" deflect — every entry is a real device).
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
- Class hierarchy (`ComputeDevice` → `GpuDevice`, `CpuDevice`, future `NpuDevice`) is the single extensibility seam. Adding a backend = one new subclass. No conditional logic, no lookup tables.
- Vendor classification stays in one place (`contracts/host.py`). The collector never duplicates the vendor-id-to-name mapping.
- "Default device" is always a concrete device on a real host — no implicit variant cascade fallback, no "no pin" path to reason about.

Negative:

- The dropdown shows "Vulkan device 0" for AMD/Intel devices, which is opaque to operators. Accepted — `vulkaninfo` is not universally installed, and shipping a Vulkan probe is a dependency we do not want today. When llama.cpp reports "no Vulkan device N" the operator knows to pick a different index.
- Per-model `device` can be set even when the resolved binary does not match the vendor (the override UI does not enforce compatibility — the cmd layer silently no-ops on an unknown `(variant, vendor)` pair). Documented; not a footgun because the cmd just runs without the env var.
- `default_device` is a *named device position*, not a stable identity. If the operator swaps the GPU in slot 0 for a different model, the saved `default_device` still points to index 0 — possibly wrong device. We document this; we do not persist UUIDs (no portable way to obtain them across vendors).
- There is no UI affordance for "no pin" / "unset" on a host with GPUs. The Status page shows only real devices, with the smart default pre-selected. Programmatic `set_default_device(None)` is still supported and reverts to the smart pick on the next render, but operators have to reach for the API. This is intentional — "use default" was misleading; one deterministic choice is better than many.
- Field rename (`gpu` → `device`) breaks any pre-refactor `overrides.yaml` files that used the `gpu:` key. Backward-compat reader accepts both keys for one cycle; the loader migrates `gpu` → `device` on read. One-time deprecation, same pattern used elsewhere in the framework.

Neutral:

- The collector runs `nvidia-smi` once (was once for `-L`, still once — folded into `--query-gpu`). No additional subprocess calls in the hot path.
- `Recipe.gpu` is a new optional field. Bundled recipes do not set it; default is `None`. User recipes can set it.
- `Hardware.devices` ordering follows collector enumeration order, which is whatever the runtime returns. Stable across calls (`lru_cache`), not stable across reboots or device hot-plug. Documented as expected.

## Plan

[`docs/arch/plans/plan-039-multi-gpu-llama-swap.md`](plans/plan-039-multi-gpu-llama-swap.md) — file-by-file execution in four independently testable phases.
