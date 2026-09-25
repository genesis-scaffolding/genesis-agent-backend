# Plan-039: Multi-GPU llama-swap

File-by-file execution in four phases. Each phase is independently testable; phases 2–4 depend on phase 1's contract addition.

## Phase 1 — Contract + collector

Files:

- `genesis_worker/contracts/host.py`
  - Add `GpuDevice` frozen dataclass with `vendor: Literal["nvidia","amd","intel"]`, `index: int`, `label: str`.
  - Add `devices: tuple[GpuDevice, ...] = ()` field to `Hardware`.
  - Update module docstring to describe the new field.
- `genesis_worker/utils/collectors/hardware.py`
  - Replace `_nvidia_smi_count()` with `_nvidia_smi_devices()` returning `tuple[GpuDevice, ...]`. Single subprocess call: `nvidia-smi --query-gpu=index,name --format=csv,noheader,nounits`. Parse each line into a `GpuDevice`.
  - Compute `nvidia_count` from `len(devices)` — no second subprocess.
  - Build AMD/Intel devices from the PCI enumeration. Sequential per-vendor indices; labels = `f"Vulkan device {i}"`.
  - Populate `devices` in `collect_hardware_info()`. Stable order: NVIDIA first, then AMD, then Intel.
- `genesis_worker/tests/test_hardware.py`
  - Add tests:
    - `devices` empty on no-GPU host.
    - NVIDIA enumeration multi-device from the new `--query-gpu` mock.
    - AMD enumeration (single + multiple).
    - Intel enumeration (single + multiple).
    - Mixed NVIDIA + Intel (the laptop case).
  - Update existing `_nvidia_smi_count` mocks to the new query-gpu format (one stub helper, shared across tests).

Gate: `uv run pytest genesis_worker/tests/test_hardware.py -q`.

## Phase 2 — Service option

Files:

- `genesis_worker/services/llama_swap/options.py`
  - Add `default_gpu: GpuDevice | None = None` field.
- `genesis_worker/services/llama_swap/service.py`
  - Import `GpuDevice` from `contracts`.
  - Update `_auto_resolve()` per ADR §2.
  - Add private helper `_variant_for_vendor(vendor: str) -> str | None` returning `"cuda"`, `"vulkan"`, or `None`.
  - Add public `set_default_gpu(gpu: GpuDevice | None)` write method, mirroring `set_llama_server_variant`.
  - Add public `default_gpu` property for read access.
  - Add public `host_info` property (read-through to `self._ctx.host_info`) so UI pages do not reach into the private context.
- `genesis_worker/tests/test_service_llama_swap.py`
  - Add tests:
    - `default_gpu=None` preserves the existing cascade.
    - `default_gpu` set + matching binary installed → returns the binary.
    - `default_gpu` set + matching binary missing → raises with a clear message.
    - `default_gpu` with an unknown vendor → raises.
    - `set_default_gpu` round-trips through the option slice.
    - `set_default_gpu` validates the device against `host_info.hardware.devices`.

Gate: `uv run pytest genesis_worker/tests/test_service_llama_swap.py -q`.

## Phase 3 — Cmd emission + override schema

Files:

- `genesis_worker/services/llama_swap/recipes.py`
  - Add `gpu: GpuDevice | None = None` field to `Recipe`.
- `genesis_worker/services/llama_swap/generate_config.py`
  - Add `binary_variant: str | None = None` to `BuildOptions`.
  - Add `gpu: GpuDevice | None = None` to `EvaluatedConfig`.
  - Add module-level `_ENV_VAR_FOR` constant + `_env_prefix(variant, gpu)` helper.
  - Update `evaluate_recipe` to thread `options.binary_variant` and resolve `gpu` with the standard `override > recipe > default > None` cascade. Add `gpu` to the `provenance` dict.
  - Update `cmd_from_evaluated_dict` to prepend `_env_prefix(...)` to the rendered cmd.
  - Update `cmd_from_evaluated` wrapper to pass `binary_variant` through.
- `genesis_worker/tests/test_generate_config.py`
  - Add tests:
    - No `gpu` selected → no prefix (byte-identical cmd).
    - `gpu=nvidia` + `binary_variant="cuda"` → `CUDA_VISIBLE_DEVICES=N` prefix.
    - `gpu=amd` + `binary_variant="vulkan"` → `GGML_VK_VISIBLE_DEVICES=N` prefix.
    - `gpu=intel` + `binary_variant="vulkan"` → same prefix.
    - `gpu` set but `binary_variant=None` (legacy fallback) → no prefix.
    - `gpu` set but unknown `(variant, vendor)` combo → no prefix (silent no-op).
    - Provenance key `"gpu"` correctly tracks `OVERRIDE / RECIPE / DEFAULT / COMPUTED` across sources.

Gate: `uv run pytest genesis_worker/tests/test_generate_config.py -q`.

## Phase 4 — UI surfaces

Files:

- `genesis_worker/services/llama_swap/ui/status.py`
  - Add "Default GPU" container mirroring the existing "Variant" container.
  - Dropdown populated from `svc.host_info.hardware.devices`, prefixed with "(use cascade)".
  - On change, calls `svc.set_default_gpu(value)` then `worker.regenerate_service_config(SERVICE_NAME)`.
- `genesis_worker/services/llama_swap/ui/config_editor.py`
  - Add "GPU" dropdown to `_render_override_form`.
  - Populated from `svc.host_info.hardware.devices`, prefixed with "(use service default)" (= `None`).
  - Save path unchanged.
- Manual smoke: start worker, change default GPU, regenerate config, verify the env var appears in the rendered cmd. Repeat for per-model override.

Gate: manual smoke + existing UI tests (`uv run pytest genesis_worker/tests/ -q`).

## Final

- Four-gate: `uv run pytest -q && uv run pyright && uv run ruff check genesis_worker && uv run ruff format --check genesis_worker`.
- If implementation diverges from the ADR, update the ADR first (per AGENTS.md).
- Show the user the final diff; wait for approval before committing.
- Commit granularity: docs commit (ADR + plan) first, then one commit per phase (P1 / P2 / P3 / P4). Reviewable history.
- Merge `feature/multi-gpu-llama-swap` into `main` with `--no-ff`. Push to `origin`.
