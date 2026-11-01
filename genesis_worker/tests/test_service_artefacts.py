"""Tests for the artefact route (ADR-043).

``GET /v1/services/{name}/artefacts/{artefact}`` serves a document a
service plugin generates. The llama-swap plugin serves the pi
``models.json``; every other service serves nothing.

The worker is hermetic: isolated dirs per test, the module-level worker
singleton is reset, and host probes are stubbed (no network). The
llama-swap model evaluation is stubbed too, so the tests assert on the
export shape without depending on a scanned catalogue.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genesis_worker import GenesisWorker
from genesis_worker.api import deps
from genesis_worker.api.app import create_app
from genesis_worker.contracts import HostInfo
from genesis_worker.services.llama_swap.generate_config import EvaluatedConfig
from genesis_worker.settings import PathsSettings, Settings

_FIXED_HOST = HostInfo(
    hostname="test-host",
    os="Linux 6.5",
    arch="x86_64",
    python="3.11.7",
    uptime_s=3600,
    public_ip=None,
    tailscale_ip=None,
    hardware=HostInfo.empty().hardware,
)


def _cfg(entry_id: str = "qwen-27B", *, ctx_size: int = 40960) -> EvaluatedConfig:
    from genesis_worker.services.llama_swap.generate_config import DetectedFileSet

    return EvaluatedConfig(
        chat_template_kwargs={},  # type: ignore[arg-type]
        name=entry_id,
        entry_id=entry_id,
        matched_recipe=None,
        binary="ignored",
        files=DetectedFileSet(
            main=Path("/tmp/m.gguf"),
            filename=f"{entry_id}.gguf",
            mmproj=None,
            draft=None,
            is_mtp=False,
            weight_bytes=0,
        ),
        kv_cache=None,
        mmproj_offload=None,
        spec=None,
        ctx_min=None,
        parallel=None,
        reasoning_budget=None,
        reasoning_budget_message=None,
        chat_template_file=None,
        sampling={},
        provenance={},
        cmd="",
        ctx_size=ctx_size,
    )


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[GenesisWorker, None, None]:
    monkeypatch.setattr(
        "genesis_worker.utils.collectors.host_info.collect_host_info",
        lambda: _FIXED_HOST,
    )
    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
            vault_path=tmp_path / "vault",
        )
    )
    w = GenesisWorker(settings=settings)
    deps._worker = w
    yield w
    deps._worker = None


@pytest.fixture
def client(worker: GenesisWorker) -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def stub_models(worker: GenesisWorker, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the llama-swap evaluation with one synthetic model."""
    svc = worker.service("llama_swap")
    monkeypatch.setattr(
        svc,
        "evaluate_model_config",
        lambda catalog: {"qwen-27B": _cfg("qwen-27B")},
    )


# --- happy path --------------------------------------------------------------


def test_artefact_route_serves_the_models_document(client: TestClient, stub_models: None) -> None:
    r = client.get("/v1/services/llama_swap/artefacts/models.json")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body
    provider = next(iter(body["providers"].values()))
    assert provider["models"][0]["id"] == "qwen-27B"
    assert provider["api"] == "openai-completions"


def test_artefact_route_honours_the_provider_param(client: TestClient, stub_models: None) -> None:
    r = client.get("/v1/services/llama_swap/artefacts/models.json?provider=p3")
    assert r.status_code == 200
    body = r.json()
    assert list(body["providers"]) == ["p3"]
    assert body["providers"]["p3"]["models"][0]["id"] == "p3/qwen-27B"


def test_artefact_route_keeps_metadata_under_prefixing(
    client: TestClient, stub_models: None
) -> None:
    bare = client.get("/v1/services/llama_swap/artefacts/models.json").json()
    bare_model = next(iter(bare["providers"].values()))["models"][0]
    addressed = client.get("/v1/services/llama_swap/artefacts/models.json?provider=p3").json()
    addressed_model = addressed["providers"]["p3"]["models"][0]
    for field in ("contextWindow", "input", "maxTokens", "reasoning", "cost"):
        assert addressed_model[field] == bare_model[field]


# --- 404s --------------------------------------------------------------------


def test_unknown_service_is_404(client: TestClient) -> None:
    r = client.get("/v1/services/nope/artefacts/models.json")
    assert r.status_code == 404
    assert "unknown service" in r.json()["detail"]


def test_unknown_artefact_is_404(client: TestClient, stub_models: None) -> None:
    r = client.get("/v1/services/llama_swap/artefacts/nope.json")
    assert r.status_code == 404
    assert "serves no artefact" in r.json()["detail"]


def test_service_without_artefacts_is_404(client: TestClient) -> None:
    """A service that generates nothing answers 404, not 500."""
    r = client.get("/v1/services/bifrost/artefacts/models.json")
    assert r.status_code == 404
    assert "serves no artefact" in r.json()["detail"]
