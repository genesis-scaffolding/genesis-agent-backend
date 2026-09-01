# Plan 032: Centralize host hardware detection; pass to plugins via PluginContext

## Problem

GPU detection is currently scattered across two services with
copy-pasted implementations, and AMD/iGPU isn't detected anywhere:

- `genesis_worker/services/comfyui/service.py::_has_nvidia_gpu()` —
  runs `nvidia-smi -L`, caches at construction.
- `genesis_worker/services/llama_swap/service.py::_has_nvidia_gpu()` —
  same function, copy-pasted.
- `DockerContainer.nvidia_runtime_available()` — separate concern, only
  checks whether the Docker runtime plugin is installed.
- No AMD/iGPU detection anywhere.
- `HostInfo` exists in the framework but doesn't carry hardware info;
  services can't reach it through `ServiceContext`.

The duplication is fragile. When Gen gets an AMD box, the framework
currently reports "no GPU" for everything, including llama-swap's
auto variant picker (which would happily fall through to vulkan on
AMD — but the present code reads NVIDIA-only).

## What we're building

### 1. New `Hardware` dataclass

`genesis_worker/utils/models.py`:

```python
@dataclass(frozen=True)
class Hardware:
    """Snapshot of host GPUs and accelerators, no runtime state."""

    # NVIDIA — what we detect today.
    nvidia: bool                       # any NVIDIA GPU on the host
    nvidia_count: int                  # >=0; 0 when none
    nvidia_driver_loaded: bool         # /proc/driver/nvidia/version exists

    # AMD — new. Both discrete and iGPUs detected.
    amd: bool
    amd_count: int
    amd_vendor_id_present: bool        # saw 0x1002 in /sys/class/drm

    # Intel iGPU — new. Common on laptops.
    intel_igpu: bool
    intel_count: int

    # Docker-side concerns — useful for "should I even try GPU?" decisions.
    nvidia_runtime: bool               # `docker info` reports nvidia runtime

    def vendor_summary(self) -> str:
        """One-line dashboard summary: 'NVIDIA (1) + AMD (1)' or 'None'."""
```

`HostInfo` gains `hardware: Hardware` (default `Hardware.empty()` so the
existing constructor stays simple for tests).

### 2. New `collect_hardware_info()` in the framework

`genesis_worker/utils/collectors/hardware.py`:

Probe order (cheap first, expensive last). All probes best-effort;
missing kernel modules, no `/sys/class/drm`, no `nvidia-smi` → return
`False`/`0` for that field, never raise.

1. **NVIDIA driver loaded?** — `os.path.exists("/proc/driver/nvidia/version")`
2. **NVIDIA device count** — `nvidia-smi -L` parsing; falls back to 0 when no binary.
3. **AMD/iGPU vendor enumeration** — glob `/sys/class/drm/card*/device/vendor`,
   read each file, map hex `0x1002` → AMD, `0x8086` → Intel, `0x10de` →
   NVIDIA (cross-check). Avoids needing `lspci`.
4. **NVIDIA runtime** — `docker info` substring check. (Same probe as
   `DockerContainer.nvidia_runtime_available` today; we move the
   detection up to the framework so the answer is shared.)

Process-level cache via `functools.lru_cache(maxsize=1)` on the
collector. Probing once per worker startup, not per Streamlit rerun.

`collect_host_info()` is updated to also call `collect_hardware_info()`
and stuff the result into `HostInfo.hardware`. One function, one probe,
shared everywhere.

### 3. `PluginContext` carries `host_info`

`genesis_worker/contracts/context.py`:

- `PluginContext` adds `host_info: HostInfo` field with a default of
  `HostInfo.empty()`. The default keeps existing test fixtures and
  plugin authors' `super().__init__(ctx)` calls working without
  re-plumbing.
- `HostInfo.empty()` classmethod returns a sentinel with hostname=""
  and hardware=`Hardware.empty()` (all zeros / False). Plugin code
  that wants the real deal reads `self._ctx.host_info.hardware.nvidia`.
- The framework populates it in `Registry._common_kwargs(cls)`:

```python
def _common_kwargs(self, cls):
    return {
        ...,
        "host_info": _collect_or_default_host_info(),
    }
```

- `_factories.py::service_ctx` and `source_ctx` accept
  `host_info: HostInfo | None = None` and pass through.

### 4. ComfyUI: drop the local probe

- `ComfyUiService.__init__` removes the `subprocess` import and the
  cached `self._has_nvidia_gpu` field.
- All `self._has_nvidia_gpu` references become
  `self._ctx.host_info.hardware.nvidia`.
- The `has_nvidia_gpu` property is preserved as a thin alias
  (`return self._ctx.host_info.hardware.nvidia`) so existing UI
  (`svc.has_nvidia_gpu` in `ui/status.py` and `ui/image.py`) keeps
  compiling without churn. Marked as deprecated in docstring; not
  removed because the public-API surface area is documented in
  tests and might be referenced from other places.
- The start() guard becomes:
  `if self._options.gpu_required and not self._ctx.host_info.hardware.nvidia: ...`
- The `runtime`/`gpu_flags` decision uses the same field plus
  `host_info.hardware.nvidia_runtime`.

### 5. llama-swap: drop the local probe

- `LlamaSwapService._has_nvidia_gpu()` and its helper are deleted.
- `_auto_resolve()` reads `self._ctx.host_info.hardware.nvidia`.
- The variant auto-resolution logic stays:
  **NVIDIA + cuda installed → cuda; else vulkan; else cpu.**
  On AMD-only hosts, this naturally lands on vulkan (ROCm surfaces
  through Vulkan on AMD), which is the right answer.

### 6. Dashboard surface

`genesis_worker/ui/dashboard.py` "About" panel adds a third line
alongside OS/Python:

```
**GPU:** NVIDIA (1) · driver loaded
```

Or `**GPU:** AMD (1) · Intel iGPU (1)` for the AMD+laptop combo,
or `**GPU:** none detected`.

### 7. Tests

- **New** `genesis_worker/tests/test_hardware.py` — unit tests for
  `collect_hardware_info` with mocked `/sys/class/drm` files,
  mocked `subprocess.run` for `nvidia-smi`, mocked `docker info`.
  Covers: NVIDIA-only, AMD-only, Intel-only, multi-vendor,
  no-GPU, missing-bins, mixed-present-and-missing.
- **Update** `genesis_worker/tests/test_host_info.py` — assert
  `info.hardware` is a `Hardware` instance.
- **Update** `genesis_worker/tests/test_comfyui_service.py` — replace
  the 5 `monkeypatch.setattr("..._has_nvidia_gpu", ...)` sites with
  `monkeypatch.setattr(svc._ctx.host_info.hardware, "nvidia", True)`.
  Or, cleaner: change `_has_nvidia_gpu` to a no-op shim during the
  refactor and let the test fixtures inject `host_info` directly.
- **Update** `genesis_worker/tests/test_service_llama_swap.py` —
  the `monkeypatch.setattr(svc, "_has_nvidia_gpu", ...)` lines
  become `monkeypatch.setattr(svc._ctx.host_info.hardware, "nvidia", True)`.
- **No change** to `tests/_factories.py` signatures — the new
  `host_info` arg is optional.

### 8. Scope boundaries — explicitly NOT in this branch

- **AMD Docker runtime plumbing.** ComfyUI currently hardcodes
  `runtime="nvidia"`, `gpu_driver="nvidia"`. For an AMD box we'd
  need `--device /dev/dri /dev/kfd` passthrough and likely a
  ROCm-flavoured image. That's a separate, larger task. This branch
  makes the *detection* uniform; the runtime plumbing is a follow-up.
- **Service-specific AMD code paths.** llama-swap's auto variant
  already lands on vulkan for AMD (ROCm works through Vulkan).
  ComfyUI gets detection only — its start() still refuses on
  `gpu_required=True` if no NVIDIA GPU is present. That stays
  correct until the AMD Docker runtime work lands.
- **`collect_metrics` (NVIDIA-only VRAM).** The metrics collector
  already swallows `pynvml` failures. Leave it alone.

## Why one shared snapshot vs probe-per-request

Three reasons:

1. **Cost.** `nvidia-smi` takes 100-300 ms. The dashboard's "About"
   panel calls `collect_host_info()` every render. Probing once and
   caching for the lifetime of the worker is the right cost.
2. **Consistency.** Two services probing independently could see
   different answers if a hotplug happens mid-test. One snapshot,
   shared, consistent.
3. **Single source of truth.** When Gen plugs in an external GPU on
   a laptop, the framework can log the change in one place; the
   next worker restart reflects it across the dashboard, llama-swap
   auto-pick, and ComfyUI install gating.

## Files

### Schema + collection

- `genesis_worker/utils/models.py` — `Hardware` dataclass + `HostInfo.hardware` + `HostInfo.empty()` classmethod
- `genesis_worker/utils/collectors/hardware.py` — **new** — `collect_hardware_info()`, `@lru_cache` wrapper
- `genesis_worker/utils/collectors/host_info.py` — call `collect_hardware_info()` and set `HostInfo.hardware`

### Contract

- `genesis_worker/contracts/context.py` — `PluginContext.host_info: HostInfo = field(default_factory=HostInfo.empty)`

### Framework plumbing

- `genesis_worker/registries.py` — `_common_kwargs(cls)` populates `host_info`
- `genesis_worker/tests/_factories.py` — `service_ctx`/`source_ctx` accept `host_info=None`

### Service cleanup

- `genesis_worker/services/comfyui/service.py` — drop `_has_nvidia_gpu`, `subprocess` import; thread `self._ctx.host_info.hardware` through `start()` and the `has_nvidia_gpu` shim
- `genesis_worker/services/llama_swap/service.py` — drop `_has_nvidia_gpu`, thread `self._ctx.host_info.hardware` through `_auto_resolve()`

### UI

- `genesis_worker/ui/dashboard.py` — add GPU line in "About" panel
- (No change to `ui/status.py` for comfyui or llama-swap — the `svc.has_nvidia_gpu` shim keeps the existing rendering working.)

### Tests

- `genesis_worker/tests/test_hardware.py` — **new** — vendor detection matrix
- `genesis_worker/tests/test_host_info.py` — assert `hardware` populated
- `genesis_worker/tests/test_comfyui_service.py` — rewrite 5 `_has_nvidia_gpu` monkeypatches → `hardware.nvidia`
- `genesis_worker/tests/test_service_llama_swap.py` — rewrite 1 monkeypatch → `hardware.nvidia`

## Commit sequence

1. `feat(framework): add Hardware dataclass + collect_hardware_info()` (schema + collector + tests)
2. `feat(framework): pass host_info to plugins via PluginContext` (context + registries + factories)
3. `refactor(comfyui): use ctx.host_info.hardware instead of local probe`
4. `refactor(llama-swap): use ctx.host_info.hardware instead of local probe`
5. `feat(ui): show GPU vendor summary on dashboard`

5 commits on `feature/centralize-host-hardware`. Each commit leaves
the gate green independently; no half-broken states in `main` if
you bisect.

## Open question for review

**Do you want AMD enumeration to require root?** `/sys/class/drm/card*/device/vendor`
is world-readable on most distros (0444). On some hardened setups
it's root-only. If a user's `/sys/class/drm` is unreadable, we
silently return `amd=False`. That's probably fine — it's a
best-effort probe, and an absent vendor just means "this framework
can't tell you about your AMD card." Sound acceptable?

If yes to all of the above, I'll proceed with the implementation.
