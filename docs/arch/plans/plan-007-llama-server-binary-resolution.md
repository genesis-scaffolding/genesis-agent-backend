# Plan 007: llama-server binary resolution

Implements [spec-007](../specs/spec-007-llama-server-binary-resolution.md).

## Working rules

- Branch: `feature/service-install` (continuing from plan-005/006).
- No new dependencies.
- The running llama-swap on `:8080` is **not** stopped. The tests use a scratch install root.
- `bin/`, `Makefile`, `config.yaml`, `MODEL_CATALOG.{yaml,md}`, `pi-models.json` are **not** modified (ADR-008). `recipes.yaml` is now modified (the spec lifts the freeze for this file — see spec §File changes).
- One commit at the end. Wait for user verification before commit (AGENTS.md).
- Validation gates: `uv run pytest -q`, `uv run pyright`, `uv run ruff check genesis_worker`. Plus the boundary walker: `uv run pytest -q genesis_worker/tests/test_plugin_boundary.py`.

## Step 1: recipes.yaml — drop the default's binary

`genesis_worker/services/llama_swap/data/recipes.yaml`:

```yaml
  default:
-   binary: "vendor/llama.cpp/build/bin/llama-server"   # stock llama.cpp
    ctx_min: 131072                   # 128k floor for --fit-ctx (2^17)
    parallel: 1
    sampling:
      temp: 0.8
      top_p: 0.95
      top_k: 40
```

The default recipe now has no `binary` field. Bonsai's stays. No other recipe has a `binary` set.

## Step 2: options — add `llama_server_variant`

`genesis_worker/services/llama_swap/options.py`:

```python
from typing import Literal

class LlamaSwapOptions(BaseModel):
    ...
    llama_server_variant: Literal["auto", "cuda", "cpu", "vulkan"] | None = None
```

## Step 3: BuildOptions — add `default_binary`

`genesis_worker/services/llama_swap/generate_config.py`:

```python
@dataclass(frozen=True)
class BuildOptions:
    repo_root: Path
    kv_quant_over: int = DEFAULT_KV_QUANT_OVER
    mmproj_offload_over: int = DEFAULT_MMPROJ_OFFLOAD_OVER
    default_binary_rel: str = DEFAULT_BINARY_REL
    default_binary: str | None = None    # NEW
```

Cascade update in `evaluate_recipe`:

```python
binary_str = (
    ovr.get("binary")
    or recipe.binary
    or options.default_binary                              # NEW
    or binary_override
    or (default_recipe.binary if default_recipe else None)
    or options.default_binary_rel
)
```

Provenance update: `FieldSource.COMPUTED` for the new tier (it's a service-level resolution, not a recipe value).

## Step 4: service — variant resolution

`genesis_worker/services/llama_swap/service.py`:

- Drop `self._build_options = BuildOptions(...)` from `__init__`. Replace with a method `self._build_options()` that constructs fresh.
- Add the methods listed in spec §File changes.
- `regenerate_config` and `evaluate_model_config` call `self._build_options()`.
- Imports: `from typing import Literal`, `import subprocess`.

```python
def _default_llama_server_binary(self) -> str | None:
    variant = self.llama_server_variant
    if variant is None:
        return None
    if variant == "auto":
        return self._auto_resolve()
    return self._variant_binary(f"llama-server-{variant}")

def _auto_resolve(self) -> str | None:
    if self._has_nvidia_gpu():
        binary = self._variant_binary("llama-server-cuda")
        if binary:
            return binary
    binary = self._variant_binary("llama-server-vulkan")
    if binary:
        return binary
    return self._variant_binary("llama-server-cpu")

def _has_nvidia_gpu(self) -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True, timeout=5, text=True,
        )
        return result.returncode == 0 and "GPU" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

def _variant_binary(self, name: str) -> str | None:
    for installable in self.installs():
        if installable.name == name:
            bp = installable.binary_path()
            if bp is not None:
                return str(bp)
    return None

def _build_options(self) -> BuildOptions:
    return BuildOptions(
        repo_root=self._ctx.repo_root,
        kv_quant_over=self._options.kv_quant_over_bytes,
        mmproj_offload_over=self._options.mmproj_offload_over_bytes,
        default_binary=self._default_llama_server_binary(),
        default_binary_rel=self._options.default_binary_rel,
    )

def is_ready_to_serve(self) -> bool:
    if self._default_llama_server_binary() is not None:
        return True
    return self._legacy_binary_exists()

def _legacy_binary_exists(self) -> bool:
    legacy = self._options.default_binary_rel
    if legacy is None:
        return False
    path = Path(legacy)
    if not path.is_absolute():
        path = self._ctx.repo_root / path
    return path.is_file()

@property
def llama_server_variant(self) -> str | None:
    return self._options.llama_server_variant

def set_llama_server_variant(self, variant: str | None) -> None:
    self._options.llama_server_variant = variant
```

Pydantic `BaseModel` allows attribute assignment for non-validated fields (the `Literal[...] | None` is a validator, but assignment to a previously-validated field is fine).

## Step 5: config editor — dropdown override

`genesis_worker/services/llama_swap/ui/config_editor.py`:

- Replace the `text_input` for binary with a `selectbox` + conditional `text_input`.

```python
if cfg.binary is not None:
    variant_options = ["(use cascade)", "Custom path…"]
    variant_values = [None, "__custom__"]
    for installable in svc.installs():
        if not installable.name.startswith("llama-server-"):
            continue
        bp = installable.binary_path()
        if bp is None:
            continue
        variant_options.append(f"{installable.name} ({bp})")
        variant_values.append(str(bp))

    current = current_overrides.get("binary")
    if current is None:
        current_idx = 0
    elif current in variant_values:
        current_idx = variant_values.index(current)
    else:
        current_idx = 1  # Custom path

    choice = st.selectbox(
        "Binary",
        variant_options,
        index=current_idx,
        key=f"ov-{entry_id}-binary",
    )
    if choice == "(use cascade)":
        pass
    elif choice == "Custom path…":
        custom_default = current if current not in variant_values else ""
        new_overrides["binary"] = st.text_input(
            "Custom binary path",
            value=custom_default,
            key=f"ov-{entry_id}-binary-custom",
        )
    else:
        idx = variant_options.index(choice)
        new_overrides["binary"] = variant_values[idx]
```

The "Regenerate config" button at the top of the page is gated on `svc.is_ready_to_serve()`.

## Step 6: status page — variant picker

`genesis_worker/services/llama_swap/ui/status.py`:

- New "Variant" section between "Service info" and "Binaries".
- `selectbox` with `(legacy)`, `auto`, `cuda`, `cpu`, `vulkan`. `on_change` writes via `svc.set_llama_server_variant(...)`.
- Status line: `Resolved: <path>` (green) / `Falling back to legacy: <path>` (yellow) / `No llama-server binary available` (red).
- The "Regenerate config" button at the top is gated on `svc.is_ready_to_serve()`.

```python
def _on_variant_change() -> None:
    new = st.session_state["status-variant"]
    svc.set_llama_server_variant(None if new == "(legacy)" else new)

with st.container(border=True):
    st.subheader("Variant")
    current = svc.llama_server_variant or "(legacy)"
    options = ["(legacy)", "auto", "cuda", "cpu", "vulkan"]
    choice = st.selectbox(
        "llama-server variant",
        options,
        index=options.index(current),
        key="status-variant",
        on_change=_on_variant_change,
    )
    resolved = svc._default_llama_server_binary()  # may be private — see notes
    if resolved is not None:
        st.success(f"Resolved: {resolved}")
    elif svc._options.default_binary_rel:
        st.warning(f"No variant matched. Falling back to legacy: {svc._options.default_binary_rel}")
    else:
        st.error("No llama-server binary available. Install via Binaries.")
```

The `_default_llama_server_binary` access is private; either expose it as `effective_llama_server_binary()` (recommended) or have the page ask the service for `(resolved, legacy)` as a tuple. We'll add a public method.

## Step 7: tests

`tests/test_generate_config.py`:

- `test_default_binary_in_cascade`: 'options.default_binary' wins over 'default_recipe.binary' and 'default_binary_rel', loses to per-model 'recipe.binary'.
- `test_default_binary_none_falls_through_to_legacy`: 'options.default_binary=None' keeps the legacy path.

`tests/test_service_llama_swap.py`:

- `test_variant_resolution_explicit_installed`: installs a llama-server-cuda stub and asserts the resolved path.
- `test_variant_resolution_explicit_missing`: variant="cuda" with no install returns None.
- `test_variant_resolution_auto_picks_cuda_when_nvidia`: monkey-patches `_has_nvidia_gpu` to True, installs cuda, asserts cuda.
- `test_variant_resolution_auto_picks_vulkan_when_no_nvidia`: monkey-patches `_has_nvidia_gpu` to False, installs vulkan, asserts vulkan.
- `test_variant_resolution_auto_falls_back_to_cpu`: no NVIDIA + no vulkan + cpu installed.
- `test_variant_resolution_auto_returns_none_when_nothing_installed`.
- `test_is_ready_to_serve_true_with_variant_installed`.
- `test_is_ready_to_serve_false_with_no_variant_and_missing_legacy`.
- `test_set_llama_server_variant_persists_in_service_options`.
- `test_evaluate_model_config_uses_framework_binary_for_qwen_recipe`: loads a qwen3.6 entry, asserts the resolved binary is the framework-managed path.

`tests/test_recipes.py` (file may not exist; check):

- `test_default_recipe_has_no_binary`: parse `data/recipes.yaml`, assert `default.binary is None`.

## Step 8: gates

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run pytest -q genesis_worker/tests/test_plugin_boundary.py
```

## Step 9: manual verification

1. With CUDA installed (current workstation), on the Status page:
   - Pick `auto` → status shows `Resolved: <llama-server-cuda path>`.
   - Pick `cuda` → same path.
   - Pick `vulkan` (not installed) → status shows `Falling back to legacy: vendor/llama.cpp/...` (yellow).
   - Pick `cpu` (not installed) → same fallback.
   - Pick `(legacy)` → `Resolved: <legacy path>` (if exists) or warning.
   - Pick `auto` again → resolves to cuda.
2. Click "Regenerate config" — config is written.
3. On the Config editor, expand a qwen3.6 model. The "Binary" row is a selectbox with options. Pick `llama-server-cuda` → override is set. Switch to `Custom path…` → text input appears.
4. Verify the binary is resolved correctly via the effective-config table.

## Step 10: commit

Single commit. Message: `llama-swap: resolve binary from framework-managed variants with auto-detect (spec-007)`.

Files: spec, plan, recipes.yaml, options.py, generate_config.py, service.py, config_editor.py, status.py, tests.

## Notes

- `_default_llama_server_binary` is private. The Status page needs to read it. We expose a public `effective_llama_server_binary()` method that returns the resolved path. Same value, public API.
- pydantic `BaseModel` allows attribute assignment by default. The `set_llama_server_variant` method writes to `self._options.llama_server_variant = variant`. If pydantic v2 enforces immutability in some flag, we may need to construct a new `LlamaSwapOptions` instance — but defaults are mutable.
- The `nvidia-smi` probe is called on every config regen. The cost is one process spawn (~100ms). Acceptable; the framework is not regenerating at high frequency.
