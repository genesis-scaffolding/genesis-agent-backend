# Spec 007: llama-server binary resolution

## Goal

The generated llama-swap config currently points at the legacy `vendor/llama.cpp/build/bin/llama-server` path baked into the bundled recipes. Now that the framework manages three llama-server variants (cuda / cpu / vulkan) via the install surface from spec-005, the config generator should:

1. Resolve the per-machine "default" llama-server binary from the framework's installed variants.
2. Pick the variant via a service-level setting (`auto` / `cuda` / `cpu` / `vulkan` / `legacy`).
3. Auto-detect hardware: NVIDIA → try CUDA, else Vulkan, else CPU (last resort).
4. Refuse config generation when no llama-server binary is reachable — the user has to install a variant or set the legacy fallback to a valid path.
5. Replace the per-model "binary path" text input in the config editor with a dropdown listing installed variants.

The per-machine variant pick is a single source of truth that the user can change in the UI; the per-model override is a UI escape hatch for advanced users.

## File changes

### `services/llama_swap/data/recipes.yaml`

Drop the default recipe's `binary: "vendor/llama.cpp/build/bin/llama-server"` line. The recipes.yaml freeze (ADR-008) was scoped to the legacy bootstrap path; the new design is intentional and the file is no longer load-bearing for binary resolution.

Keep bonsai's `binary: "vendor/prism-llama.cpp/build/bin/llama-server"` — that's a per-model override for the prism-llama.cpp fork, which the framework does not manage. The cascade still picks it up via `recipe.binary`.

### `services/llama_swap/options.py`

Add:

```python
llama_server_variant: Literal["auto", "cuda", "cpu", "vulkan"] | None = "auto"
```

Reads from `GENESIS_SERVICES__LLAMA_SWAP__LLAMA_SERVER_VARIANT`. Default is `"auto"` so the framework-managed binary wins without a one-time setup step — the user can pin `(legacy)` in the UI to opt out. `None` = legacy fallback (existing behavior).

### `services/llama_swap/service.py`

New methods:

```python
def _default_llama_server_binary(self) -> str | None:
    """Resolve the configured variant to an installed binary path.

    ``None`` for the variant setting returns ``None`` (caller falls back
    to ``default_binary_rel``). The "auto" branch calls :meth:`_auto_resolve`.
    Explicit ``cuda`` / ``cpu`` / ``vulkan`` returns the matching
    installable's binary path, or ``None`` if not installed.
    """

def _auto_resolve(self) -> str | None:
    """Priority: NVIDIA present + cuda installed → cuda; else vulkan; else cpu.

    CPU is the last resort. None if nothing is installed.
    """

def _has_nvidia_gpu(self) -> bool:
    """Probe ``nvidia-smi -L``. Robust against missing-binary and hangs."""

def _variant_binary(self, name: str) -> str | None:
    """Look up an installed variant by its installable name (e.g. ``llama-server-cuda``)."""

def _build_options(self) -> BuildOptions:
    """Build :class:`BuildOptions` with ``default_binary`` re-resolved.

    Re-resolved on every call so newly installed variants are picked up
    on the next config regen without restarting the worker.
    """

def is_ready_to_serve(self) -> bool:
    """True iff a llama-server binary is reachable for config generation.

    Checked: variant binary installed, or legacy ``default_binary_rel`` resolves
    to an existing file. Refuses to generate config when this is False.
    """

def _legacy_binary_exists(self) -> bool:
    """Check the legacy fallback path against ``repo_root`` for existence."""

@property
def llama_server_variant(self) -> str | None:
    """Read the current variant setting (env-default, possibly UI-overridden)."""

def set_llama_server_variant(self, variant: str | None) -> None:
    """UI write path. Mutates the service's options in place.
    ``None`` reverts to legacy fallback; ``"auto"`` re-enables detection.
    """
```

`regenerate_config` and `evaluate_model_config` are updated to call `self._build_options()` instead of carrying the options as an instance attribute. This is what makes the dynamic re-resolution work.

`_has_nvidia_gpu` shells out:

```python
def _has_nvidia_gpu(self) -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True, timeout=5, text=True,
        )
        return result.returncode == 0 and "GPU" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
```

`FileNotFoundError` (no `nvidia-smi` on PATH) → False; falls through to Vulkan/CPU. The user can still pick `cuda` explicitly if they know the binary is installed.

### `services/llama_swap/generate_config.py`

Add `default_binary: str | None = None` to `BuildOptions`. The cascade:

```python
binary_str = (
    ovr.get("binary")
    or recipe.binary
    or options.default_binary                                    # NEW
    or binary_override
    or (default_recipe.binary if default_recipe else None)
    or options.default_binary_rel
)
```

The service-managed binary beats the (now-None) default recipe's binary but loses to per-model recipes (bonsai → prism-llama.cpp). `default_binary_rel` stays as the final safety net.

Add `--kv-unified` to the hardcoded flags. llama.cpp's default for `kv_unified` is "enabled if number of slots is auto" — when the user pins `parallel=1` (the default recipe's value), the binary defaults to `kv_unified=false`. The previous vendor-built binary had it on by default; the ai-dock b10375 build does not. The framework injects `--kv-unified` so the cmd is stable across binary versions. The flag is also added to `EvaluatedConfig.hardcoded_flags` so the config editor surfaces it under "Hardcoded flags (always)".

### `services/llama_swap/ui/config_editor.py`

Replace the `text_input` for binary with a `selectbox`:

| Option | Stored value |
|--|--|
| `(use cascade)` | cleared — override removed |
| `llama-server-cuda` (or whichever variant is installed) | installable's `binary_path()` |
| `…-cpu` | … |
| `…-vulkan` | … |
| `Custom path…` | appears + a `text_input` whose value is the override |

The displayed labels include the resolved path so the user sees the binary path inline. The stored value is a path string (same as the current text-input behavior). Variant reinstalls make the stored path stale — the user re-selects; out of scope to track a variant symbol through reinstalls.

The regenerate button at the top of the page is gated on `svc.is_ready_to_serve()`.

### `services/llama_swap/ui/status.py`

New "Variant" section near the top:

- A `selectbox` with `(legacy)`, `auto`, `cuda`, `cpu`, `vulkan`. The current value comes from `svc.llama_server_variant`.
- `on_change` writes to `svc.set_llama_server_variant(...)`. No Save button — the change is applied immediately.
- A status line: `Resolved: <path>` (green) when the variant resolved, or `Falling back to legacy: <path>` (yellow) when the variant is missing but a legacy path exists, or `No llama-server binary available — install via Binaries` (red) when nothing is reachable.
- The "Regenerate config" button is gated on `svc.is_ready_to_serve()`.

The Binaries section below is unchanged.

## Tests

- `test_default_binary_in_cascade`: `BuildOptions(default_binary="/x/llama-server")` overrides `default_recipe.binary` and `default_binary_rel`, loses to per-model `recipe.binary`.
- `test_default_binary_none_falls_through_to_legacy`: `BuildOptions(default_binary=None)` keeps the legacy path as the resolved binary.
- `test_variant_resolution_explicit_installed`: setting `llama_server_variant="cuda"` returns the cuda installable's path when installed.
- `test_variant_resolution_explicit_missing`: setting `llama_server_variant="cuda"` returns `None` when not installed.
- `test_variant_resolution_auto_picks_cuda_when_nvidia`: with a fake "NVIDIA present" + cuda installed, auto picks cuda.
- `test_variant_resolution_auto_picks_vulkan_when_no_nvidia`: no NVIDIA + vulkan installed, auto picks vulkan.
- `test_variant_resolution_auto_falls_back_to_cpu`: no NVIDIA + no vulkan + cpu installed, auto picks cpu.
- `test_variant_resolution_auto_returns_none_when_nothing_installed`: nothing installed → None.
- `test_is_ready_to_serve_true_with_variant_installed`: ready.
- `test_is_ready_to_serve_false_with_no_variant_and_missing_legacy`: not ready.
- `test_set_llama_server_variant_persists_in_service_options`: setting via API updates the property.
- `test_evaluate_model_config_uses_framework_binary_for_qwen_recipe`: integration — a qwen3.6 entry evaluates to the framework-managed path, not the legacy vendor path.
- `test_cmd_emits_kv_unified_hardcoded`: cmd contains `--kv-unified` and the flag is in `hardcoded_flags`.
- `test_cmd_emits_kv_unified_even_with_recipe_binary`: per-model recipe binary doesn't suppress the hardcoded flags.
- `test_variant_resolution_default_is_auto`: `LlamaSwapOptions().llama_server_variant == "auto"`.

`nvidia-smi` probe is covered by mocking `subprocess.run`. The `recipes.yaml` change is tested implicitly by the cascade tests; an explicit YAML parse test pins the absence of the default's binary.

UI: manual exercise of the variant dropdown (Status page) and override dropdown (Config editor).

## Consequences

- `data/recipes.yaml` is no longer a pinned path source. The recipes file becomes a config-template file only; binary resolution lives in the service.
- The `LlamaSwapService` gains a runtime dependency on the installable state. The variant install/uninstall cycle is reflected in the next config regen without a worker restart.
- Two new affordances on the UI: a per-machine variant picker (Status page) and a per-model override dropdown (Config editor).
- The `default_binary_rel` option stays as a final safety net. Users who haven't migrated to the variant workflow can keep using the legacy path.

## Verification

- `uv run pytest -q` — all pass.
- `uv run pyright` — 0 errors.
- `uv run ruff check genesis_worker` — clean.
- `uv run pytest -q genesis_worker/tests/test_plugin_boundary.py` — clean.
- Manual: with CUDA installed, on the Status page pick `auto` → status shows `Resolved: <llama-server-cuda path>`. Pick `cuda` explicitly → same path. Pick `vulkan` (not installed) → status shows `Falling back to legacy` + warning. The "Regenerate config" button is disabled when no binary is reachable.
- Manual: on the Config editor, expand a qwen3.6 model. The "Binary" row is a selectbox. Pick `llama-server-cuda` → override is set. Switch to `Custom path…` → text input appears.

## Out of scope

- File-persistent variant setting (session-state only for v1 — env var is the canonical source).
- Adding a `llama-server-rocm` / `llama-server-sycl` variant. Same framework mechanism; future spec.
- Per-model override as a variant symbol (e.g. override stores `"cuda"` instead of a path). The current implementation re-resolves to a path on click; reinstall of the variant can make the override stale. Tracking this is out of scope.
- A `Settings` page for service-level config. For now, env vars + Status-page dropdown.
