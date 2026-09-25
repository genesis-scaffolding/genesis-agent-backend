"""Tests for generate_config: structured view (EvaluatedConfig) + cmd builder.

Covers :func:`evaluate_recipe`, :func:`cmd_from_evaluated`,
:func:`evaluate_all`, and :func:`walk_models`. The YAML write path
(build_config / build_entry / write_config) is covered in
:mod:`test_config_emit`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.contracts.catalog import Catalog, ModelEntry, ModelPiece
from genesis_worker.services.llama_swap.generate_config import (
    BuildOptions,
    DetectedFileSet,
    EvaluatedConfig,
    FieldSource,
    detect_file_sets,
    evaluate_all,
    evaluate_recipe,
)
from genesis_worker.services.llama_swap.recipes import (
    Recipe,
    Recipes,
)


@pytest.fixture
def options(tmp_path: Path) -> BuildOptions:
    return BuildOptions(repo_root=tmp_path)


def _recipe(
    *,
    name: str = "test",
    binary: str | None = None,
    sampling: dict | None = None,
    chat_template_file: str | None = None,
    chat_template_kwargs: dict | None = None,
    parallel: int | None = None,
    spec: dict | None = None,
    kv_cache: str | None = None,
    mmproj_offload: bool | None = None,
    ctx_min: int | None = None,
    ctx_size: int | None = None,
    reasoning_budget: int | None = None,
    reasoning_budget_message: str | None = None,
) -> Recipe:
    return Recipe(
        name=name,
        binary=binary,
        sampling=sampling if sampling is not None else {},
        chat_template_file=chat_template_file,
        chat_template_kwargs=chat_template_kwargs if chat_template_kwargs is not None else {},
        parallel=parallel,
        spec=spec,
        kv_cache=kv_cache,
        mmproj_offload=mmproj_offload,
        ctx_min=ctx_min,
        ctx_size=ctx_size,
        reasoning_budget=reasoning_budget,
        reasoning_budget_message=reasoning_budget_message,
    )


def _evaluate(recipe: Recipe, options: BuildOptions, **kw) -> EvaluatedConfig:
    file_sets = detect_file_sets(_entry())
    files = file_sets[0]
    return evaluate_recipe(
        recipe,
        files,
        entry_id="test",
        name="Test",
        options=options,
        **kw,
    )


# ---------------------------------------------------------------------------
# evaluate_recipe — basic fields
# ---------------------------------------------------------------------------


def test_evaluate_recipe_returns_none_for_unset_fields(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = _evaluate(recipe, options)
    assert evaluated.parallel is None
    assert evaluated.kv_cache is None
    assert evaluated.ctx_min is None
    assert evaluated.sampling == {}


def test_evaluate_recipe_uses_recipe_values(options: BuildOptions) -> None:
    recipe = _recipe(parallel=2, kv_cache="q8_0", ctx_min=131072)
    evaluated = _evaluate(recipe, options)
    assert evaluated.parallel == 2
    assert evaluated.kv_cache == "q8_0"
    assert evaluated.ctx_min == 131072


def test_evaluate_recipe_override_wins(options: BuildOptions) -> None:
    recipe = _recipe(parallel=2, kv_cache="q8_0")
    evaluated = _evaluate(recipe, options, overrides={"parallel": 5, "kv_cache": "q4_0"})
    assert evaluated.parallel == 5
    assert evaluated.kv_cache == "q4_0"


def test_evaluate_recipe_falls_back_to_default_recipe(options: BuildOptions) -> None:
    recipe = _recipe()  # no parallel
    default = _recipe(parallel=4)
    evaluated = _evaluate(recipe, options, default_recipe=default)
    assert evaluated.parallel == 4
    assert evaluated.provenance["parallel"] == FieldSource.DEFAULT


def test_evaluate_recipe_override_blocks_default_fallback(options: BuildOptions) -> None:
    recipe = _recipe()
    default = _recipe(parallel=4)
    evaluated = _evaluate(recipe, options, default_recipe=default, overrides={"parallel": 7})
    assert evaluated.parallel == 7
    assert evaluated.provenance["parallel"] == FieldSource.OVERRIDE


# ---------------------------------------------------------------------------
# Size-based fallbacks
# ---------------------------------------------------------------------------


def test_kv_cache_falls_back_to_q8_0_for_large_file(options: BuildOptions) -> None:
    """Size-based fallback fires when no override/recipe/default sets kv_cache."""
    recipe = _recipe()  # kv_cache not set
    files = detect_file_sets(_entry())[0]
    large_files = DetectedFileSet(
        main=files.main,
        filename=files.filename,
        mmproj=files.mmproj,
        draft=files.draft,
        is_mtp=files.is_mtp,
        weight_bytes=30_000_000_000,  # > 25 GB
    )
    evaluated = evaluate_recipe(recipe, large_files, entry_id="test", name="Test", options=options)
    assert evaluated.kv_cache == "q8_0"


def test_mmproj_offload_falls_back_to_true_for_large_file(options: BuildOptions) -> None:
    recipe = _recipe()
    files = detect_file_sets(_entry_with_mmproj())[0]
    large_files = DetectedFileSet(
        main=files.main,
        filename=files.filename,
        mmproj=files.mmproj,
        draft=files.draft,
        is_mtp=files.is_mtp,
        weight_bytes=30_000_000_000,
    )
    evaluated = evaluate_recipe(recipe, large_files, entry_id="test", name="Test", options=options)
    assert evaluated.mmproj_offload is True


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_marks_override(options: BuildOptions) -> None:
    recipe = _recipe(parallel=1)
    evaluated = _evaluate(recipe, options, overrides={"parallel": 9})
    assert evaluated.provenance["parallel"] == FieldSource.OVERRIDE


def test_provenance_marks_recipe(options: BuildOptions) -> None:
    recipe = _recipe(parallel=1)
    evaluated = _evaluate(recipe, options)
    assert evaluated.provenance["parallel"] == FieldSource.RECIPE


def test_provenance_marks_default(options: BuildOptions) -> None:
    recipe = _recipe()
    default = _recipe(parallel=1)
    evaluated = _evaluate(recipe, options, default_recipe=default)
    assert evaluated.provenance["parallel"] == FieldSource.DEFAULT


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def test_binary_from_override(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = _evaluate(recipe, options, overrides={"binary": "/custom/path/server"})
    assert evaluated.binary == "/custom/path/server"
    assert evaluated.provenance["binary"] == FieldSource.OVERRIDE


def test_binary_falls_back_to_default_rel(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = _evaluate(recipe, options)
    expected = str((options.repo_root / "vendor/llama.cpp/build/bin/llama-server").resolve())
    assert evaluated.binary == expected
    assert evaluated.provenance["binary"] == FieldSource.COMPUTED


def test_default_binary_in_cascade(tmp_path: Path) -> None:
    """``options.default_binary`` wins over the default recipe's binary and the legacy path."""
    from genesis_worker.services.llama_swap.recipes import Recipe

    default_binary = str(tmp_path / "framework-managed-llama-server")
    options = BuildOptions(
        repo_root=tmp_path,
        default_binary=default_binary,
    )
    recipe = _recipe()
    default_recipe = Recipe(name="default", binary="vendor/somewhere/llama-server")
    evaluated = _evaluate(recipe, options, default_recipe=default_recipe)
    assert evaluated.binary == default_binary
    assert evaluated.provenance["binary"] == FieldSource.COMPUTED


def test_default_binary_none_falls_through_to_legacy(options: BuildOptions) -> None:
    """``default_binary=None` keeps the legacy path as the resolved binary."""
    recipe = _recipe()
    evaluated = _evaluate(recipe, options)
    expected = str((options.repo_root / "vendor/llama.cpp/build/bin/llama-server").resolve())
    assert evaluated.binary == expected


def test_default_binary_loses_to_recipe_binary(tmp_path: Path) -> None:
    """A per-model recipe's binary (e.g. bonsai) beats the service-managed default."""
    options = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(tmp_path / "framework-llama-server"),
    )
    recipe = _recipe(binary="vendor/prism-llama.cpp/build/bin/llama-server")
    evaluated = _evaluate(recipe, options)
    assert evaluated.binary == str(
        (tmp_path / "vendor/prism-llama.cpp/build/bin/llama-server").resolve()
    )
    assert evaluated.provenance["binary"] == FieldSource.RECIPE


def test_cmd_emits_kv_unified_hardcoded(options: BuildOptions) -> None:
    """``--kv-unified`` is hardcoded; b10375's default is off for ``n_slots=1``."""
    recipe = _recipe()
    evaluated = _evaluate(recipe, options)
    assert "--kv-unified" in evaluated.cmd
    assert "--kv-unified" in evaluated.hardcoded_flags


def test_cmd_emits_kv_unified_even_with_recipe_binary(tmp_path: Path) -> None:
    """Per-model recipe binary doesn't suppress the hardcoded flags."""
    options = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(tmp_path / "framework-llama-server"),
    )
    recipe = _recipe(binary="vendor/prism-llama.cpp/build/bin/llama-server")
    evaluated = _evaluate(recipe, options)
    assert "--kv-unified" in evaluated.cmd


# ---------------------------------------------------------------------------
# Sampling / chat_template_kwargs (no default-recipe fallback)
# ---------------------------------------------------------------------------


def test_sampling_uses_recipe_dict(options: BuildOptions) -> None:
    recipe = _recipe(sampling={"temp": 0.7, "top_p": 0.9})
    evaluated = _evaluate(recipe, options)
    assert evaluated.sampling == {"temp": 0.7, "top_p": 0.9}
    assert evaluated.provenance["sampling"] == FieldSource.RECIPE


def test_sampling_override_wins(options: BuildOptions) -> None:
    recipe = _recipe(sampling={"temp": 0.7})
    evaluated = _evaluate(recipe, options, overrides={"sampling": {"temp": 0.3}})
    assert evaluated.sampling == {"temp": 0.3}
    assert evaluated.provenance["sampling"] == FieldSource.OVERRIDE


def test_chat_template_kwargs_empty_when_unset(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = _evaluate(recipe, options)
    assert evaluated.chat_template_kwargs is None or evaluated.chat_template_kwargs == {}


# ---------------------------------------------------------------------------
# evaluate_all — catalog walk with _is_llm_candidate filter
# ---------------------------------------------------------------------------


def test_evaluate_all_filters_out_image_models(tmp_path: Path) -> None:
    """The walker applies _is_llm_candidate; image entries don't reach the UI."""
    catalog = Catalog(
        root="/tmp/vault",
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash="x",
        entries=[
            # GGUF — should pass filter and (with a matching recipe) yield an entry
            _gguf_entry("foo/llm-gguf"),
            # Image model (no GGUF pieces) — should be filtered out
            _image_entry("foo/some-sdxl"),
        ],
    )
    recipes = Recipes(default=_recipe(parallel=1), matchable=[])
    out = evaluate_all(catalog, recipes, overrides={}, options=BuildOptions(repo_root=tmp_path))
    # Entry ID from piece filename: "llm-gguf.gguf" → strip .gguf → "llm-gguf"
    assert set(out) == {"llm-gguf"}


def test_evaluate_all_returns_empty_when_no_match(tmp_path: Path) -> None:
    catalog = Catalog(
        root="/tmp/vault",
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash="x",
        entries=[_gguf_entry("foo/llm")],
    )
    recipes = Recipes(default=None, matchable=[])
    out = evaluate_all(catalog, recipes, overrides={}, options=BuildOptions(repo_root=tmp_path))
    assert out == {}


def test_evaluate_all_includes_overrides(tmp_path: Path) -> None:
    catalog = Catalog(
        root="/tmp/vault",
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash="x",
        entries=[_gguf_entry("foo/llm")],
    )
    recipes = Recipes(default=_recipe(parallel=1), matchable=[])
    # Key is from piece filename: "llm.gguf" → "llm" after .gguf strip
    out = evaluate_all(
        catalog,
        recipes,
        overrides={"llm": {"parallel": 99}},
        options=BuildOptions(repo_root=tmp_path),
    )
    assert out["llm"].parallel == 99
    assert out["llm"].provenance["parallel"] == FieldSource.OVERRIDE


# ---------------------------------------------------------------------------
# Chat template file resolution
# ---------------------------------------------------------------------------


def test_chat_template_file_absolute_passes_through(options: BuildOptions) -> None:
    recipe = _recipe(chat_template_file="/tmp/my-template.jinja")
    evaluated = _evaluate(recipe, options)
    assert "--chat-template-file /tmp/my-template.jinja" in evaluated.cmd


def test_chat_template_file_relative_resolves_from_package_dir(
    options: BuildOptions,
) -> None:
    """Relative paths resolve from the bundled recipes package directory."""
    recipe = _recipe(chat_template_file="gemma-4-chat-template.jinja")
    evaluated = _evaluate(recipe, options)
    assert "--chat-template-file" in evaluated.cmd
    # The resolved path must point inside the package, not the repo root.
    assert "/genesis_worker/services/llama_swap/data/gemma-4-chat-template.jinja" in evaluated.cmd


def test_chat_template_file_not_emitted_when_unset(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = _evaluate(recipe, options)
    assert "--chat-template-file" not in evaluated.cmd


def test_chat_template_file_non_existent_does_not_raise(options: BuildOptions) -> None:
    """Non-existent resolved paths are emitted without error (llama-server fails at runtime)."""
    recipe = _recipe(chat_template_file="does-not-exist.jinja")
    evaluated = _evaluate(recipe, options)
    assert "--chat-template-file" in evaluated.cmd


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _entry() -> ModelEntry:
    return ModelEntry(
        name="test/foo",
        source="huggingface",
        pieces=[
            ModelPiece(
                role="main",
                filename="m.gguf",
                path=Path("/tmp/vault/test/foo/m.gguf"),
                bytes=1_000_000_000,
            ),
        ],
        total_bytes=1_000_000_000,
        directory="/tmp/vault/test/foo",
        notes=[],
        extra={},
    )


def _entry_with_mmproj() -> ModelEntry:
    return ModelEntry(
        name="test/foo-mmproj",
        source="huggingface",
        pieces=[
            ModelPiece(
                role="main",
                filename="m.gguf",
                path=Path("/tmp/vault/test/foo-mmproj/m.gguf"),
                bytes=1_000_000_000,
            ),
            ModelPiece(
                role="mmproj",
                filename="mmproj.gguf",
                path=Path("/tmp/vault/test/foo-mmproj/mmproj.gguf"),
                bytes=1,
            ),
        ],
        total_bytes=1_000_000_001,
        directory="/tmp/vault/test/foo-mmproj",
        notes=[],
        extra={},
    )


def _gguf_entry(name: str) -> ModelEntry:
    """A minimal HF entry that passes _is_llm_candidate (has a .gguf piece)."""
    bare = name.split("/", 1)[-1]
    return ModelEntry(
        name=name,
        source="huggingface",
        pieces=[
            ModelPiece(
                role="main",
                filename=f"{bare}.gguf",
                path=Path(f"/tmp/vault/{name}/{bare}.gguf"),
                bytes=1,
            )
        ],
        total_bytes=1,
        directory=f"/tmp/vault/{name}",
        notes=[],
        extra={},
    )


def _image_entry(name: str) -> ModelEntry:
    """A non-LLM HF entry (no .gguf pieces) — should be filtered out."""
    bare = name.split("/", 1)[-1]
    return ModelEntry(
        name=name,
        source="huggingface",
        pieces=[
            ModelPiece(
                role="main",
                filename=f"{bare}.safetensors",
                path=Path(f"/tmp/vault/{name}/{bare}.safetensors"),
                bytes=1,
            )
        ],
        total_bytes=1,
        directory=f"/tmp/vault/{name}",
        notes=[],
        extra={},
    )


# ---------------------------------------------------------------------------
# ctx_size — built-in -c cap
# ---------------------------------------------------------------------------


def test_evaluate_recipe_uses_ctx_size_when_set(options: BuildOptions) -> None:
    recipe = _recipe(ctx_size=40960)
    evaluated = _evaluate(recipe, options)
    assert evaluated.ctx_size == 40960
    assert evaluated.provenance["ctx_size"] == FieldSource.RECIPE


def test_evaluate_recipe_ctx_size_none_when_unset(options: BuildOptions) -> None:
    recipe = _recipe()
    evaluated = _evaluate(recipe, options)
    assert evaluated.ctx_size is None
    assert evaluated.provenance["ctx_size"] == FieldSource.COMPUTED


def test_evaluate_recipe_ctx_size_override_wins(options: BuildOptions) -> None:
    recipe = _recipe(ctx_size=40960)
    evaluated = _evaluate(recipe, options, overrides={"ctx_size": 8192})
    assert evaluated.ctx_size == 8192
    assert evaluated.provenance["ctx_size"] == FieldSource.OVERRIDE


def test_evaluate_recipe_ctx_size_falls_back_to_default_recipe(
    options: BuildOptions,
) -> None:
    recipe = _recipe()
    default = _recipe(ctx_size=65536)
    evaluated = _evaluate(recipe, options, default_recipe=default)
    assert evaluated.ctx_size == 65536
    assert evaluated.provenance["ctx_size"] == FieldSource.DEFAULT


def test_cmd_emits_c_flag_when_ctx_size_set(options: BuildOptions) -> None:
    recipe = _recipe(ctx_size=40960)
    evaluated = _evaluate(recipe, options)
    assert "-c 40960" in evaluated.cmd


def test_cmd_omits_c_flag_when_ctx_size_none(options: BuildOptions) -> None:
    recipe = _recipe(ctx_min=131072)
    evaluated = _evaluate(recipe, options)
    # -c is absent; --fit-ctx is still emitted.
    assert " -c " not in evaluated.cmd
    assert "--fit-ctx 131072" in evaluated.cmd
    assert " -c 0" not in evaluated.cmd
    assert " -c 1" not in evaluated.cmd


def test_cmd_emits_c_after_fit_ctx_in_runtime_section(options: BuildOptions) -> None:
    """``-c`` sits between ``--fit-ctx`` and ``--parallel`` in the cmd."""
    recipe = _recipe(ctx_min=131072, ctx_size=40960, parallel=2)
    evaluated = _evaluate(recipe, options)
    cmd = evaluated.cmd
    pos_fit = cmd.find("--fit-ctx")
    pos_c = cmd.find(" -c ")
    pos_par = cmd.find("--parallel")
    assert pos_fit != -1 and pos_c != -1 and pos_par != -1
    assert pos_fit < pos_c < pos_par


def test_cmd_c_flag_only_when_both_fit_ctx_and_ctx_size_set(
    options: BuildOptions,
) -> None:
    """When both min and cap are present, both flags appear; the cap can clamp the floor."""
    recipe = _recipe(ctx_min=131072, ctx_size=40960)
    evaluated = _evaluate(recipe, options)
    assert "--fit-ctx 131072" in evaluated.cmd
    assert "-c 40960" in evaluated.cmd


# ---------------------------------------------------------------------------
# gpu field + env-var cmd prefix (ADR-039 §3, §4)
# ---------------------------------------------------------------------------


def _gpu(vendor: str = "nvidia", index: int = 0, label: str = "Test Card"):
    from genesis_worker.contracts.host import GpuDevice

    return GpuDevice(vendor=vendor, index=index, label=label)


def test_evaluate_recipe_gpu_unset_propagates_none(options: BuildOptions) -> None:
    """No gpu anywhere → no env prefix in the cmd."""
    evaluated = _evaluate(_recipe(), options)
    assert evaluated.gpu is None
    # The cmd's first line should start with the binary path, not an
    # env-var prefix. ``default_binary_rel`` is the legacy fallback,
    # resolved relative to ``repo_root``.
    first_line = evaluated.cmd.splitlines()[0]
    assert not first_line.startswith("CUDA_VISIBLE_DEVICES")
    assert not first_line.startswith("GGML_VK_VISIBLE_DEVICES")


def test_cmd_no_env_prefix_when_gpu_unset(options: BuildOptions) -> None:
    """Single-GPU hosts that leave gpu unset keep a byte-identical cmd."""
    evaluated = _evaluate(_recipe(), options)
    assert "CUDA_VISIBLE_DEVICES" not in evaluated.cmd
    assert "GGML_VK_VISIBLE_DEVICES" not in evaluated.cmd


def test_cmd_emits_cuda_visible_devices_for_cuda_binary(
    options: BuildOptions, tmp_path: Path
) -> None:
    """binary_variant=cuda + gpu=nvidia → CUDA_VISIBLE_DEVICES=N prefix."""
    cuda_binary = tmp_path / "cuda-llama-server"
    cuda_binary.write_text("#!/bin/sh\n")
    opts = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(cuda_binary),
        binary_variant="cuda",
    )
    evaluated = _evaluate(_recipe(), opts, overrides={"gpu": _gpu("nvidia", 0, "RTX 2060")})
    assert evaluated.gpu is not None
    assert evaluated.gpu.vendor == "nvidia"
    assert evaluated.cmd.startswith(f"CUDA_VISIBLE_DEVICES=0 {cuda_binary} \\")


def test_cmd_emits_vk_visible_devices_for_vulkan_amd(options: BuildOptions, tmp_path: Path) -> None:
    """binary_variant=vulkan + gpu=amd → GGML_VK_VISIBLE_DEVICES=N prefix."""
    vk_binary = tmp_path / "vk-llama-server"
    vk_binary.write_text("#!/bin/sh\n")
    opts = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(vk_binary),
        binary_variant="vulkan",
    )
    evaluated = _evaluate(_recipe(), opts, overrides={"gpu": _gpu("amd", 0, "Vulkan device 0")})
    assert evaluated.cmd.startswith(f"GGML_VK_VISIBLE_DEVICES=0 {vk_binary} \\")


def test_cmd_emits_vk_visible_devices_for_vulkan_intel(
    options: BuildOptions, tmp_path: Path
) -> None:
    vk_binary = tmp_path / "vk-llama-server"
    vk_binary.write_text("#!/bin/sh\n")
    opts = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(vk_binary),
        binary_variant="vulkan",
    )
    evaluated = _evaluate(_recipe(), opts, overrides={"gpu": _gpu("intel", 0, "Vulkan device 0")})
    assert evaluated.cmd.startswith(f"GGML_VK_VISIBLE_DEVICES=0 {vk_binary} \\")


def test_cmd_emits_uses_specific_index_not_always_zero(
    options: BuildOptions, tmp_path: Path
) -> None:
    """Index 2 → CUDA_VISIBLE_DEVICES=2."""
    cuda_binary = tmp_path / "cuda-llama-server"
    opts = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(cuda_binary),
        binary_variant="cuda",
    )
    evaluated = _evaluate(_recipe(), opts, overrides={"gpu": _gpu("nvidia", 2, "third card")})
    assert evaluated.cmd.startswith(f"CUDA_VISIBLE_DEVICES=2 {cuda_binary} \\")


def test_cmd_no_prefix_when_variant_legacy(options: BuildOptions) -> None:
    """binary_variant=None (legacy fallback) → no env prefix regardless of gpu."""
    evaluated = _evaluate(_recipe(), options, overrides={"gpu": _gpu()})
    assert "CUDA_VISIBLE_DEVICES" not in evaluated.cmd
    assert "GGML_VK_VISIBLE_DEVICES" not in evaluated.cmd


def test_cmd_no_prefix_when_vendor_mismatch(options: BuildOptions, tmp_path: Path) -> None:
    """gpu=amd but binary is cuda → unknown combo, no prefix (silent no-op)."""
    cuda_binary = tmp_path / "cuda-llama-server"
    opts = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(cuda_binary),
        binary_variant="cuda",
    )
    evaluated = _evaluate(_recipe(), opts, overrides={"gpu": _gpu("amd", 0, "mismatched")})
    # Should fall back to no prefix (the cmd layer does not crash on
    # unknown (variant, vendor) pairs; the service layer is the place
    # that fails loud if the binary doesn't match the gpu vendor).
    assert "CUDA_VISIBLE_DEVICES" not in evaluated.cmd
    assert "GGML_VK_VISIBLE_DEVICES" not in evaluated.cmd
    # The cmd still works (no prefix, just the binary).
    assert evaluated.cmd.startswith(f"{cuda_binary} \\")


def test_cmd_no_prefix_when_cpu_binary(options: BuildOptions, tmp_path: Path) -> None:
    """binary_variant=cpu + any gpu → no prefix (CPU doesn't honor either env var)."""
    cpu_binary = tmp_path / "cpu-llama-server"
    opts = BuildOptions(
        repo_root=tmp_path,
        default_binary=str(cpu_binary),
        binary_variant="cpu",
    )
    evaluated = _evaluate(_recipe(), opts, overrides={"gpu": _gpu("nvidia", 0, "ignored")})
    assert "CUDA_VISIBLE_DEVICES" not in evaluated.cmd


def test_gpu_override_wins_over_recipe(options: BuildOptions) -> None:
    """Per-model override beats recipe's gpu field."""
    recipe_gpu = _gpu("nvidia", 1, "recipe device")
    override_gpu = _gpu("nvidia", 0, "override device")
    recipe = _recipe()
    recipe.gpu = recipe_gpu
    evaluated = _evaluate(recipe, options, overrides={"gpu": override_gpu})
    assert evaluated.gpu == override_gpu
    assert evaluated.provenance["gpu"] == FieldSource.OVERRIDE


def test_gpu_recipe_wins_over_default_recipe(options: BuildOptions, tmp_path: Path) -> None:
    default_gpu = _gpu("nvidia", 2, "default device")
    recipe_gpu = _gpu("nvidia", 1, "recipe device")
    recipe = _recipe()
    recipe.gpu = recipe_gpu
    default_recipe = Recipe(name="default", gpu=default_gpu)
    evaluated = evaluate_recipe(
        recipe,
        detect_file_sets(_entry())[0],
        entry_id="test",
        name="Test",
        options=options,
        default_recipe=default_recipe,
    )
    assert evaluated.gpu == recipe_gpu
    assert evaluated.provenance["gpu"] == FieldSource.RECIPE


def test_gpu_default_recipe_used_when_recipe_unset(options: BuildOptions, tmp_path: Path) -> None:
    default_gpu = _gpu("amd", 0, "default device")
    default_recipe = Recipe(name="default", gpu=default_gpu)
    evaluated = evaluate_recipe(
        _recipe(),
        detect_file_sets(_entry())[0],
        entry_id="test",
        name="Test",
        options=options,
        default_recipe=default_recipe,
    )
    assert evaluated.gpu == default_gpu
    assert evaluated.provenance["gpu"] == FieldSource.DEFAULT


def test_gpu_computed_when_nothing_set(options: BuildOptions) -> None:
    """No override, recipe, or default_recipe has gpu → COMPUTED."""
    evaluated = _evaluate(_recipe(), options)
    assert evaluated.gpu is None
    assert evaluated.provenance["gpu"] == FieldSource.COMPUTED


def test_service_default_gpu_cascades_into_per_model_resolution(
    options: BuildOptions, tmp_path: Path
) -> None:
    """service_default_gpu sits in the cascade between default_recipe and None.

    The service-level option is the head honcho: every model that
    doesn't explicitly override inherits the service default. With no
    override, recipe, or default_recipe value, the cascade lands on
    options.service_default_gpu.
    """
    service_gpu = _gpu("nvidia", 0, "RTX 2060")
    opts = BuildOptions(
        repo_root=tmp_path,
        service_default_gpu=service_gpu,
    )
    evaluated = _evaluate(_recipe(), opts)
    assert evaluated.gpu == service_gpu
    # Provenance reflects the cascade level.
    assert evaluated.provenance["gpu"] == FieldSource.COMPUTED


def test_per_model_override_beats_service_default_gpu(
    options: BuildOptions, tmp_path: Path
) -> None:
    service_gpu = _gpu("nvidia", 0, "RTX 2060")
    override_gpu = _gpu("nvidia", 1, "A4000")
    opts = BuildOptions(
        repo_root=tmp_path,
        service_default_gpu=service_gpu,
    )
    evaluated = _evaluate(_recipe(), opts, overrides={"gpu": override_gpu})
    assert evaluated.gpu == override_gpu
    assert evaluated.provenance["gpu"] == FieldSource.OVERRIDE


def test_recipe_default_beats_service_default_gpu(options: BuildOptions, tmp_path: Path) -> None:
    service_gpu = _gpu("nvidia", 0, "RTX 2060")
    recipe_default_gpu = _gpu("amd", 0, "Radeon")
    opts = BuildOptions(
        repo_root=tmp_path,
        service_default_gpu=service_gpu,
    )
    default_recipe = Recipe(name="default", gpu=recipe_default_gpu)
    evaluated = evaluate_recipe(
        _recipe(),
        detect_file_sets(_entry())[0],
        entry_id="test",
        name="Test",
        options=opts,
        default_recipe=default_recipe,
    )
    assert evaluated.gpu == recipe_default_gpu
    assert evaluated.provenance["gpu"] == FieldSource.DEFAULT
