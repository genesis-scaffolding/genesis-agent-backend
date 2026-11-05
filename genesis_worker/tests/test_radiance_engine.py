"""Tests for radiance engine detection and command rendering."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from genesis_worker.services.llama_swap.engines import Engine, engine_for_file
from genesis_worker.services.llama_swap.options import RadianceOptions
from genesis_worker.services.llama_swap.radiance import (
    RadianceSettings,
    render_radiance_cmd,
    resolve_group_gids,
)
from genesis_worker.services.llama_swap.recipes import RadianceRecipe


def _settings(**overrides: object) -> RadianceSettings:
    values: dict[str, object] = {
        "image_ref": "stilldeadcode/radiance:latest",
        "group_gids": (("render", "992"), ("video", "44")),
    }
    values.update(overrides)
    return RadianceSettings(**values)  # type: ignore[arg-type]


def test_max_model_len_zero_is_valid_in_recipe_and_options() -> None:
    assert RadianceRecipe(max_model_len=0).max_model_len == 0
    assert RadianceOptions(max_model_len=0).max_model_len == 0


def test_tp_and_max_num_seqs_reject_zero() -> None:
    with pytest.raises(ValidationError):
        RadianceRecipe(tp=0)
    with pytest.raises(ValidationError):
        RadianceRecipe(max_num_seqs=0)
    with pytest.raises(ValidationError):
        RadianceOptions(tp=0)
    with pytest.raises(ValidationError):
        RadianceOptions(max_num_seqs=0)


def test_engine_for_file_detects_rad() -> None:
    assert engine_for_file(Path("model.rad")) == Engine.RADIANCE
    assert engine_for_file(Path("MODEL.RAD")) == Engine.RADIANCE


def test_engine_for_file_defaults_to_llama_server() -> None:
    assert engine_for_file(Path("model.gguf")) == Engine.LLAMA_SERVER


def test_engine_for_file_handles_none() -> None:
    assert engine_for_file(None) == Engine.LLAMA_SERVER


def test_resolve_group_gids_returns_numbers_for_known_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        name = args[-1]
        gid = {"render": "992", "video": "44"}[name]
        return subprocess.CompletedProcess(args, 0, f"{name}:x:{gid}:", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_group_gids(("render", "video")) == (("render", "992"), ("video", "44"))


def test_resolve_group_gids_omits_unknown_group(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        name = args[-1]
        if name == "render":
            return subprocess.CompletedProcess(args, 0, "render:x:992:", "")
        return subprocess.CompletedProcess(args, 2, "", "not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_group_gids(("render", "missing")) == (("render", "992"),)


def test_resolve_group_gids_returns_empty_when_getent_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_group_gids(("render", "video")) == ()


def test_render_radiance_cmd_contains_docker_run_and_model_mount() -> None:
    cmd = render_radiance_cmd(
        _settings(),
        Path("/models/qwen3.8-27b-mxfp4.rad"),
        "/models/qwen3.8-27b-mxfp4.rad",
    )
    assert cmd.startswith("docker run --init --rm")
    assert "-v /models/qwen3.8-27b-mxfp4.rad:/models/qwen3.8-27b-mxfp4.rad:ro" in cmd


def test_render_radiance_cmd_uses_numeric_group_ids() -> None:
    cmd = render_radiance_cmd(_settings(), "/host/model.rad", "/models/model.rad")
    assert "--group-add 992" in cmd
    assert "--group-add 44" in cmd
    assert "--group-add render" not in cmd


def test_render_radiance_cmd_has_no_shell_metacharacters() -> None:
    cmd = render_radiance_cmd(_settings(), "/host/model.rad", "/models/model.rad")
    for metacharacter in ("$(", ")", "|", ";", "&&", "`"):
        assert metacharacter not in cmd


def test_render_radiance_cmd_uses_rocr_visible_devices_only() -> None:
    cmd = render_radiance_cmd(_settings(), "/host/model.rad", "/models/model.rad")
    assert "-e ROCR_VISIBLE_DEVICES=0" in cmd
    assert "HIP_VISIBLE_DEVICES" not in cmd


def test_render_radiance_cmd_publishes_port_and_binds_container_port() -> None:
    cmd = render_radiance_cmd(_settings(), "/host/model.rad", "/models/model.rad")
    assert "-p ${PORT}:8000" in cmd
    assert "--port 8000" in cmd


def test_render_radiance_cmd_renders_tp_flag() -> None:
    cmd = render_radiance_cmd(_settings(tp=2), "/host/model.rad", "/models/model.rad")
    assert "--tp 2" in cmd


def test_render_radiance_cmd_renders_kv_cache_dtype_flag() -> None:
    cmd = render_radiance_cmd(
        _settings(kv_cache_dtype="fp8_v4"), "/host/model.rad", "/models/model.rad"
    )
    assert "--kv-cache-dtype fp8_v4" in cmd


def test_render_radiance_cmd_renders_max_model_len_and_max_num_seqs() -> None:
    cmd = render_radiance_cmd(
        _settings(max_model_len=32768, max_num_seqs=8),
        "/host/model.rad",
        "/models/model.rad",
    )
    assert "--max-model-len 32768" in cmd
    assert "--max-num-seqs 8" in cmd


def test_render_radiance_cmd_renders_gpu_headroom_flag() -> None:
    cmd = render_radiance_cmd(
        _settings(gpu_headroom_mib=96), "/host/model.rad", "/models/model.rad"
    )
    assert "--gpu-headroom-mib 96" in cmd
