"""Tests for the pi-agent ``models.json`` emitter.

The exporter reads straight off :class:`EvaluatedConfig`; tests
construct synthetic configs and exercise the structured-pipeline path.
No on-disk ``config.yaml`` is involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.services.llama_swap.export_pi_config import (
    DEFAULT_CONTEXT_WINDOW,
    FALLBACK_PROVIDER_NAME,
    build_provider_from_configs,
    write_models_json,
)
from genesis_worker.services.llama_swap.generate_config import EvaluatedConfig

# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _cfg(
    entry_id: str = "m",
    *,
    name: str | None = None,
    ctx_min: int | None = None,
    ctx_size: int | None = None,
    mmproj: bool = False,
    chat_template_kwargs: dict | None = None,
) -> EvaluatedConfig:
    """Build a synthetic EvaluatedConfig for pi-export tests.

    The fields exercised here are the ones the pi exporter reads.
    Everything else is left at sensible defaults.
    """
    from genesis_worker.services.llama_swap.generate_config import DetectedFileSet

    return EvaluatedConfig(
        chat_template_kwargs=chat_template_kwargs if chat_template_kwargs is not None else {},  # type: ignore[arg-type]
        name=name or entry_id,
        entry_id=entry_id,
        matched_recipe=None,
        binary="ignored",
        files=DetectedFileSet(
            main=Path("/tmp/m.gguf"),
            filename=f"{entry_id}.gguf",
            mmproj=Path("/tmp/p.gguf") if mmproj else None,
            draft=None,
            is_mtp=False,
            weight_bytes=0,
        ),
        kv_cache=None,
        mmproj_offload=None,
        spec=None,
        ctx_min=ctx_min,
        parallel=None,
        reasoning_budget=None,
        reasoning_budget_message=None,
        chat_template_file=None,
        sampling={},
        provenance={},
        cmd="",
        ctx_size=ctx_size,
    )


def _provider(configs: dict) -> dict:
    """Wrap ``build_provider_from_configs`` and pick out the inner provider dict."""
    out = build_provider_from_configs(configs)
    assert "providers" in out
    assert len(out["providers"]) == 1
    return next(iter(out["providers"].values()))


# ---------------------------------------------------------------------------
# build_provider_from_configs() shape
# ---------------------------------------------------------------------------


def test_build_provider_returns_providers_dict() -> None:
    inner = _provider({"m1": _cfg("m1")})
    assert inner["api"] == "openai-completions"
    assert inner["apiKey"] == "local"
    assert inner["compat"]["supportsDeveloperRole"] is False
    assert inner["compat"]["supportsReasoningEffort"] is False
    assert inner["compat"]["maxTokensField"] == "max_tokens"


def test_build_provider_explicit_hostname() -> None:
    out = build_provider_from_configs({}, hostname="My Box 2")
    assert next(iter(out["providers"])) == "my-box-2"


def test_build_provider_hostname_falls_back_when_empty() -> None:
    out = build_provider_from_configs({}, hostname="---")
    # _slug("---") is empty -> FALLBACK_PROVIDER_NAME
    assert next(iter(out["providers"])) == FALLBACK_PROVIDER_NAME


def test_build_provider_default_base_url() -> None:
    """Default base_url falls back to the worker's hostname (not 127.0.0.1),
    so pi-agent on another machine reaches llama-swap."""
    inner = _provider({"m": _cfg("m")})
    assert inner["baseUrl"].endswith("/v1")
    assert "127.0.0.1" not in inner["baseUrl"]


def test_build_provider_explicit_base_url() -> None:
    out = build_provider_from_configs({}, base_url="http://example.com:9000")
    inner = next(iter(out["providers"].values()))
    assert inner["baseUrl"] == "http://example.com:9000/v1"


def test_build_provider_base_url_strips_and_appends_v1() -> None:
    out = build_provider_from_configs({}, base_url="http://example.com:9000/")
    inner = next(iter(out["providers"].values()))
    assert inner["baseUrl"] == "http://example.com:9000/v1"


def test_build_provider_env_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PI_BASE_URL", "http://envhost:1234")
    inner = _provider({"m": _cfg("m")})
    assert inner["baseUrl"] == "http://envhost:1234/v1"


def test_build_provider_env_base_url_with_v1_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the env var already ends with /v1, don't double-append."""
    monkeypatch.setenv("PI_BASE_URL", "http://envhost:1234/v1")
    inner = _provider({"m": _cfg("m")})
    assert inner["baseUrl"] == "http://envhost:1234/v1"


def test_build_provider_empty_configs_emits_empty_models_list() -> None:
    inner = _provider({})
    assert inner["models"] == []


# ---------------------------------------------------------------------------
# Model field derivation
# ---------------------------------------------------------------------------


def test_model_id_and_name() -> None:
    inner = _provider({"alpha-1": _cfg("alpha-1", name="Alpha One")})
    models = inner["models"]
    assert len(models) == 1
    assert models[0]["id"] == "alpha-1"
    assert models[0]["name"] == "Alpha One"


def test_model_defaults_name_to_id_when_missing() -> None:
    inner = _provider({"naked": _cfg("naked", name="naked")})
    assert inner["models"][0]["name"] == "naked"


def test_model_context_window_from_ctx_size() -> None:
    """When ctx_size is set, it wins (the user-set cap)."""
    inner = _provider({"m": _cfg("m", ctx_size=40960)})
    assert inner["models"][0]["contextWindow"] == 40960


def test_model_context_window_from_ctx_min_when_no_ctx_size() -> None:
    inner = _provider({"m": _cfg("m", ctx_min=65536)})
    assert inner["models"][0]["contextWindow"] == 65536


def test_model_context_window_prefers_ctx_size_over_ctx_min() -> None:
    """The cap (-c) beats the floor (--fit-ctx) when both are set."""
    inner = _provider({"m": _cfg("m", ctx_min=131072, ctx_size=40960)})
    assert inner["models"][0]["contextWindow"] == 40960


def test_model_context_window_falls_back_to_default() -> None:
    """Neither set: DEFAULT_CONTEXT_WINDOW (84k)."""
    inner = _provider({"m": _cfg("m")})
    assert inner["models"][0]["contextWindow"] == DEFAULT_CONTEXT_WINDOW
    assert DEFAULT_CONTEXT_WINDOW == 84000


def test_model_input_image_when_mmproj() -> None:
    inner = _provider({"m": _cfg("m", mmproj=True)})
    assert inner["models"][0]["input"] == ["text", "image"]


def test_model_input_text_only_when_no_mmproj() -> None:
    inner = _provider({"m": _cfg("m", mmproj=False)})
    assert inner["models"][0]["input"] == ["text"]


def test_model_max_tokens_constant() -> None:
    inner = _provider({"m": _cfg("m")})
    assert inner["models"][0]["maxTokens"] == 16384


def test_model_cost_zeros() -> None:
    inner = _provider({"m": _cfg("m")})
    cost = inner["models"][0]["cost"]
    assert cost == {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}


def test_model_reasoning_default_true() -> None:
    inner = _provider({"m": _cfg("m")})
    assert inner["models"][0]["reasoning"] is True


def test_model_reasoning_false_when_enable_thinking_false() -> None:
    inner = _provider({"m": _cfg("m", chat_template_kwargs={"enable_thinking": False})})
    assert inner["models"][0]["reasoning"] is False


def test_model_reasoning_false_when_instruct_in_id() -> None:
    inner = _provider({"qwen-instruct": _cfg("qwen-instruct")})
    assert inner["models"][0]["reasoning"] is False


def test_model_reasoning_true_with_heretic_guardrail() -> None:
    """An unrelated chat_template_kwargs value doesn't disable reasoning."""
    inner = _provider({"m": _cfg("m", chat_template_kwargs={"preserve_thinking": True})})
    assert inner["models"][0]["reasoning"] is True


# ---------------------------------------------------------------------------
# write_models_json
# ---------------------------------------------------------------------------


def test_write_models_json_creates_new_file(tmp_path: Path) -> None:
    target = tmp_path / "models.json"
    provider = {"providers": {"x": {"baseUrl": "http://h/v1", "models": []}}}
    assert write_models_json(target, provider) is True
    assert target.is_file()
    assert json_loads(target) == provider


def test_write_models_json_preserves_mtime_when_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "models.json"
    provider = {"providers": {}}
    # Initial write primes the file with the emitter's exact format.
    write_models_json(target, provider)
    mtime_before = target.stat().st_mtime_ns
    assert write_models_json(target, provider) is False
    mtime_after = target.stat().st_mtime_ns
    assert mtime_after == mtime_before


def test_write_models_json_overwrites_when_changed(tmp_path: Path) -> None:
    target = tmp_path / "models.json"
    target.write_text('{"providers": {}}\n')
    provider = {"providers": {"x": {"baseUrl": "http://h/v1", "models": []}}}
    assert write_models_json(target, provider) is True
    assert json_loads(target) == provider


def json_loads(path: Path) -> dict:
    import json

    return json.loads(path.read_text())
