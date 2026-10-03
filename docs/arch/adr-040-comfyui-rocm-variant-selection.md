# ADR-040: ComfyUI ROCm variant selection — two installables, one container, variant from the GPU snapshot

## Title

ComfyUI ROCm variant selection — two installables, one container, variant from the GPU snapshot.

## Context

The ComfyUI service (ADR-025) installs and runs a single Docker image:
`ghcr.io/genesis-scaffolding/comfyui-cuda`, a CUDA build. The worker fleet now
includes an AMD GPU host, and a ROCm counterpart image exists:
`ghcr.io/genesis-scaffolding/comfyui-rocm` (built on `rocm/pytorch`, amd64
only, tag format `<version>-rocm-7.2-amd64`).

Forces:

- The ROCm image needs different `docker run` plumbing than the CUDA image:
  `--device=/dev/kfd --device=/dev/dri --group-add video` plus ROCm env vars
  (`HIP_VISIBLE_DEVICES`, empty `CUDA_VISIBLE_DEVICES`,
  `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` — load-bearing for GPU
  attention kernels). `DockerContainer.run()` (ADR-024) currently supports
  only `runtime` and `gpu_flags` — no `--device` / `--group-add`.
- The framework already exposes AMD detection: `Hardware.amd` /
  `amd_count` from `/sys/class/drm` enumeration. llama_swap (ADR-039)
  established the pattern of resolving a backend from the host snapshot
  with an optional user pin.
- Both images share the same container layout (entrypoint, bind-mount
  paths, `PUID`/`PGID`), so data dirs, symlinks, and lifecycle stay
  shared; only the image ref and GPU flags differ.
- The Image UI page (`ui/image.py`) already loops over `svc.installs()`
  and renders one expander per installable — a second installable gets
  install/uninstall/version-picker UI for free.

## Decision

We will run exactly one ComfyUI container whose image is selected by a
**GPU variant** (`cuda` | `rocm`), resolved the same way as llama_swap's
device pin: an explicit user choice (UI dropdown / `gpu_variant` option)
wins, otherwise the framework auto-picks from the host snapshot
(NVIDIA present → `cuda`, else AMD present → `rocm`, else `cuda` —
preserving today's "no NVIDIA GPU" failure message on GPU-less hosts).

Concretely:

1. **`DockerContainer.run()`** gains two additive parameters:
   `devices: list[str] | None` (→ `--device`) and
   `group_add: str | None` (→ `--group-add`). Framework-shared; no
   existing caller is affected.
2. **`ComfyUiImage` is parametrised by variant.** Each instance carries
   `variant`, its own `name` (`comfyui-cuda` / `comfyui-rocm`), its own
   repo/tag/source URL, and its own selection file at
   `state_dir/<variant>/current`. Tag arch-filtering and the 15-min
   registry cache are reused unchanged (the cache key is the repo, so
   the two variants cache independently).
3. **`ComfyUiOptions`** gains `rocm_image_repo`, `rocm_image_tag`,
   `gpu_variant` (`"cuda" | "rocm" | None`, `None` = auto-pick), and the
   ROCm container knobs (`rocm_devices`, `rocm_group_add`,
   `rocm_env`). `gpu_variant` is in-memory only, mirroring
   llama_swap's `llama_server_variant` write path.
4. **The service exposes two installables.** `installs()` returns both;
   `primary_installable`, `image_ref`, and `is_available()` resolve
   through `effective_gpu_variant`. `start()` branches on the variant:
   the CUDA path is unchanged; the ROCm path passes `devices`,
   `group_add`, and the ROCm env to the lifecycle.
5. **UI.** The Status page gets a variant dropdown (mirroring
   llama_swap's variant selectbox). The Image page disables the
   Install button per installable when the matching vendor is absent
   (NVIDIA for cuda, AMD for rocm), keeping the existing
   `gpu_required: false` escape hatch. The Status page GPU summary
   shows both vendors.

Both variants share the same bind-mounted data dirs, vault, symlink
catalog, profiles, and container name — switching variant changes only
which image the (single) container runs.

## Status

Accepted.

## Consequences

- Positive: AMD hosts get image generation out of the box; the existing
  install UI, arch filtering, cache, lifecycle, and data layout are all
  reused without modification.
- Positive: `devices` / `group_add` on `DockerContainer.run()` are
  generic and available to any future non-NVIDIA container service.
- Negative: hosts with both an NVIDIA and an AMD GPU must pick a
  variant explicitly (auto-pick prefers CUDA). Running both images
  simultaneously is out of scope — one container, one variant.
- Neutral: the ROCm image is amd64-only; the existing arch filter
  already handles this (`-arm64` hosts simply never see ROCm tags).
- Neutral: `gpu_variant` is in-memory (worker-session scoped), matching
  llama_swap. A restart reverts to auto-pick.
