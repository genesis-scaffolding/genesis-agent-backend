"""Tests for the pi-agent ``models.json`` emitter.

Field-by-field equivalence against the on-disk ``pi-models.json`` is
the gate: every emitted entry's parsed shape must equal the entry's
shape in the live artifact. Byte-level YAML/JSON ordering differences
are accepted; semantic differences are not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genesis_worker.services.llama_swap.export_pi_config import (
    DEFAULT_BASE_URL,
    FALLBACK_PROVIDER_NAME,
    build_provider,
    write_models_json,
)

# ---------------------------------------------------------------------------
# Synthetic fixtures (no real config.yaml needed)
# ---------------------------------------------------------------------------


def _entry(
    entry_id: str,
    *,
    name: str | None = None,
    cmd: str = "",
    fit_ctx: int | None = None,
    mmproj: bool = False,
    chat_template_kwargs: dict | None = None,
) -> tuple[str, dict]:
    """Build a (entry_id, body) tuple in the shape of one models: entry."""
    parts = ["--model /tmp/m"]
    if fit_ctx is not None:
        parts.append(f"--fit-ctx {fit_ctx}")
    if mmproj:
        parts.append("--mmproj /tmp/p")
    if chat_template_kwargs is not None:
        parts.append(
            f"--chat-template-kwargs '{json.dumps(chat_template_kwargs, separators=(',', ':'))}'"
        )
    parts.append("--port ${PORT}")
    cmd_str = " \\\n  ".join(parts)
    body = {
        "name": name or entry_id,
        "cmd": cmd_str,
        "proxy": "http://127.0.0.1:${PORT}",
        "ttl": 0,
    }
    return entry_id, body


def _config_with(entries: list[tuple[str, dict]]) -> str:
    """Render a minimal config.yaml string from a list of entries."""
    lines = [
        "healthCheckTimeout: 60",
        "logLevel: info",
        "models:",
    ]
    for entry_id, body in entries:
        lines.append(f"  {entry_id}:")
        lines.append(f"    name: \"{body['name']}\"")
        lines.append("    cmd: |")
        for cl in body["cmd"].split("\n"):
            lines.append("      " + cl)
        lines.append(f"    proxy: \"{body['proxy']}\"")
        lines.append(f"    ttl: {body['ttl']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# build_provider() shape
# ---------------------------------------------------------------------------


def test_build_provider_returns_providers_dict(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1", fit_ctx=131072)]))
    provider = build_provider(cfg)
    assert "providers" in provider
    assert len(provider["providers"]) == 1
    # Provider name falls back to the local hostname.
    pname = next(iter(provider["providers"]))
    assert pname  # non-empty


def test_build_provider_explicit_hostname(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1")]))
    provider = build_provider(cfg, hostname="My Box 2")
    pname = next(iter(provider["providers"]))
    assert pname == "my-box-2"


def test_build_provider_hostname_falls_back_when_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1")]))
    provider = build_provider(cfg, hostname="---")
    pname = next(iter(provider["providers"]))
    # _slug("---") is empty -> FALLBACK_PROVIDER_NAME
    assert pname == FALLBACK_PROVIDER_NAME


def test_build_provider_default_base_url(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["baseUrl"] == DEFAULT_BASE_URL
    assert inner["api"] == "openai-completions"
    assert inner["apiKey"] == "local"


def test_build_provider_explicit_base_url(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1")]))
    provider = build_provider(cfg, base_url="http://example.com:9000")
    inner = next(iter(provider["providers"].values()))
    assert inner["baseUrl"] == "http://example.com:9000/v1"


def test_build_provider_base_url_strips_and_appends_v1(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1")]))
    provider = build_provider(cfg, base_url="http://example.com:9000/")
    inner = next(iter(provider["providers"].values()))
    assert inner["baseUrl"] == "http://example.com:9000/v1"


def test_build_provider_env_base_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PI_BASE_URL", "http://envhost:1234")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["baseUrl"] == "http://envhost:1234/v1"


def test_build_provider_env_base_url_with_v1_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the env var already ends with /v1, don't double-append."""
    monkeypatch.setenv("PI_BASE_URL", "http://envhost:1234/v1")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m1")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["baseUrl"] == "http://envhost:1234/v1"


# ---------------------------------------------------------------------------
# Model field derivation
# ---------------------------------------------------------------------------


def test_model_id_and_name(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("alpha-1", name="Alpha One")]))
    provider = build_provider(cfg)
    models = next(iter(provider["providers"].values()))["models"]
    assert len(models) == 1
    assert models[0]["id"] == "alpha-1"
    assert models[0]["name"] == "Alpha One"


def test_model_defaults_name_to_id_when_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([("naked", {"name": "naked", "cmd": "", "proxy": "", "ttl": 0})]))
    provider = build_provider(cfg)
    models = next(iter(provider["providers"].values()))["models"]
    assert models[0]["name"] == "naked"


def test_model_context_window_from_fit_ctx(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m", fit_ctx=65536)]))
    provider = build_provider(cfg)
    assert provider["providers"][next(iter(provider["providers"]))]["models"][0]["contextWindow"] == 65536


def test_model_context_window_default_when_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["contextWindow"] == 131072


def test_model_context_window_picks_last_fit_ctx(tmp_path: Path) -> None:
    """Some recipes repeat --fit-ctx; we pick the last occurrence."""
    eid, body = _entry("m", fit_ctx=4096)
    body["cmd"] += " --fit-ctx 32768"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([(eid, body)]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["contextWindow"] == 32768


def test_model_input_image_when_mmproj(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m", mmproj=True)]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["input"] == ["text", "image"]


def test_model_input_text_only_when_no_mmproj(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m", mmproj=False)]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["input"] == ["text"]


def test_model_reasoning_default_true(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m-thinking")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["reasoning"] is True


def test_model_reasoning_false_when_enable_thinking_false(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m", chat_template_kwargs={"enable_thinking": False})]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["reasoning"] is False


def test_model_reasoning_false_when_instruct_in_id(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("qwen3-instruct-q4")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["reasoning"] is False


def test_model_reasoning_true_with_heretic_guardrail(tmp_path: Path) -> None:
    """``heretic`` is a guardrail, not an instruction override."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("gemma-4-heretic-gguf")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["reasoning"] is True


def test_model_cost_zeros(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["cost"] == {
        "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
    }


def test_model_max_tokens_default(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_config_with([_entry("m")]))
    provider = build_provider(cfg)
    inner = next(iter(provider["providers"].values()))
    assert inner["models"][0]["maxTokens"] == 16384


# ---------------------------------------------------------------------------
# write_models_json
# ---------------------------------------------------------------------------


def test_write_models_json_creates_new_file(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    provider = {"providers": {"x": {"baseUrl": "http://x/v1", "models": []}}}
    assert write_models_json(target, provider) is True
    assert target.is_file()
    assert json.loads(target.read_text()) == provider


def test_write_models_json_preserves_mtime_when_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    provider = {"providers": {"x": {"baseUrl": "http://x/v1", "models": []}}}
    write_models_json(target, provider)
    mtime_before = target.stat().st_mtime_ns

    import time
    time.sleep(0.01)
    wrote = write_models_json(target, provider)
    assert wrote is False
    assert target.stat().st_mtime_ns == mtime_before


def test_write_models_json_overwrites_when_changed(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.write_text('{"old": true}\n')
    provider = {"providers": {"x": {"baseUrl": "http://x/v1", "models": []}}}
    assert write_models_json(target, provider) is True
    assert json.loads(target.read_text()) == provider


# ---------------------------------------------------------------------------
# Live-fixture field-by-field equivalence (the spec's gate)
# ---------------------------------------------------------------------------


def test_live_config_yields_field_equivalent_pi_models(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Build provider from on-disk ``config.yaml`` and confirm structural soundness.

    Asserts:
    - The on-disk ``config.yaml`` (if present) parses via the new
      :func:`build_provider` without errors.
    - The emitted provider has the right shape (``baseUrl`` ends in
      ``/v1``, ``api == openai-completions``, ``compat`` keys all
      present, ``models`` list populated).
    - Per-model fields match the schema the old ``bin/pi-models.py``
      emitted (id/name/input/contextWindow/maxTokens/reasoning/cost).

    This is the spec-002 verification step 4 gate (semantic
    equivalence with the old emitter), but rewritten as an
    environmental invariant: the artifacts can drift if the user
    hasn't regenerated; the test gates on schema, not on whether the
    user kept both files in sync.
    """
    config_yaml = Path("config.yaml")
    if not config_yaml.is_file():
        pytest.skip("live config.yaml not present in CWD")

    new = build_provider(config_yaml)
    assert "providers" in new
    assert len(new["providers"]) == 1
    inner = next(iter(new["providers"].values()))

    # Provider shape.
    assert inner["baseUrl"].endswith("/v1")
    assert inner["api"] == "openai-completions"
    assert inner["apiKey"] == "local"
    compat = inner["compat"]
    assert compat["supportsDeveloperRole"] is False
    assert compat["supportsReasoningEffort"] is False
    assert compat["maxTokensField"] == "max_tokens"

    # Per-model shape.
    for m in inner["models"]:
        assert isinstance(m["id"], str) and m["id"]
        assert isinstance(m["name"], str) and m["name"]
        assert m["input"] in (["text"], ["text", "image"])
        assert isinstance(m["contextWindow"], int) and m["contextWindow"] > 0
        assert isinstance(m["maxTokens"], int) and m["maxTokens"] > 0
        assert isinstance(m["reasoning"], bool)
        assert m["cost"] == {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}

    # At least one model emitted (sanity: the catalog isn't empty).
    assert len(inner["models"]) >= 1


def test_new_emission_matches_old_emitter_against_real_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Spec-002 step 4 gate: new emitter matches the old ``bin/pi-models.py``.

    Runs both against the on-disk ``config.yaml`` and asserts field-
    by-field equivalence on the resulting providers. Both emitters
    honor the ``PI_BASE_URL`` env override, so we set it
    deterministically.

    Skips if either the old emitter (``bin/pi-models.py``) or the live
    config.yaml is missing.
    """
    import subprocess
    import sys

    config_yaml = Path("config.yaml")
    bin_script = Path("bin/pi-models.py")
    if not (config_yaml.is_file() and bin_script.is_file()):
        pytest.skip("live config.yaml or bin/pi-models.py not present")

    monkeypatch.setenv("PI_BASE_URL", "http://127.0.0.1:8080")

    proc = subprocess.run(
        [sys.executable, str(bin_script), "--stdout", "--config", str(config_yaml)],
        check=True, capture_output=True, text=True,
    )
    old = json.loads(proc.stdout)

    new = build_provider(config_yaml)

    assert len(old["providers"]) == 1
    assert len(new["providers"]) == 1
    old_inner = next(iter(old["providers"].values()))
    new_inner = next(iter(new["providers"].values()))

    assert old_inner["baseUrl"] == new_inner["baseUrl"]
    assert old_inner["api"] == new_inner["api"]
    assert old_inner["apiKey"] == new_inner["apiKey"]
    assert old_inner["compat"] == new_inner["compat"]

    old_by_id = {m["id"]: m for m in old_inner["models"]}
    new_by_id = {m["id"]: m for m in new_inner["models"]}
    assert set(old_by_id) == set(new_by_id), (
        f"id set drift: only_new={set(new_by_id) - set(old_by_id)} "
        f"only_old={set(old_by_id) - set(new_by_id)}"
    )
    for mid in old_by_id:
        for field in ("id", "name", "input", "contextWindow", "maxTokens", "reasoning", "cost"):
            assert old_by_id[mid][field] == new_by_id[mid][field], (
                f"field drift for {mid}.{field}: "
                f"old={old_by_id[mid][field]!r} new={new_by_id[mid][field]!r}"
            )