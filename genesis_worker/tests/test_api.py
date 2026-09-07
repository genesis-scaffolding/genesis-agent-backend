"""Tests for the read-only FastAPI surface (ADR-033).

Hermetic: isolated ``state_dir`` per test, the module-level worker
singleton is reset, and ``collect_host_info`` is monkeypatched to a
deterministic value (no network probes). ``TestClient`` runs the
app's lifespan, so plugin construction fires exactly as in production.
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


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[GenesisWorker, None, None]:
    """A hermetic worker wired into the API's singleton slot.

    ``monkeypatch.setattr`` on the imported symbol in
    ``utils.collectors.host_info`` — the same one the production
    code calls at construction time.
    """
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


# --- /health -----------------------------------------------------------------


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --- /v1/host ----------------------------------------------------------------


def test_host(client: TestClient) -> None:
    r = client.get("/v1/host")
    assert r.status_code == 200
    body = r.json()
    assert body["hostname"] == "test-host"
    assert body["os"] == "Linux 6.5"
    assert body["arch"] == "x86_64"
    assert "hardware" in body
    assert set(body["hardware"].keys()) >= {
        "nvidia",
        "nvidia_count",
        "amd",
        "intel_igpu",
    }


# --- /v1/metrics -------------------------------------------------------------


def test_metrics_shape(client: TestClient) -> None:
    r = client.get("/v1/metrics")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "cpu_percent",
        "ram_used_gb",
        "ram_total_gb",
        "gpu_percent",
        "vram_used_gb",
        "vram_total_gb",
    }
    # cpu/ram are always populated; gpu/vram may be None on a non-NVIDIA box.
    assert isinstance(body["cpu_percent"], (int, float))
    assert body["ram_total_gb"] > 0


# --- /v1/paths ---------------------------------------------------------------


def test_paths_reflect_isolated_settings(client: TestClient, tmp_path: Path) -> None:
    r = client.get("/v1/paths")
    assert r.status_code == 200
    body = r.json()
    assert body["state_dir"] == str(tmp_path / "state")
    assert body["vault"] == str(tmp_path / "vault")
    assert body["data_dir"] == str(tmp_path / "data")


# --- /v1/sources -------------------------------------------------------------


def test_sources_list(client: TestClient) -> None:
    r = client.get("/v1/sources")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    # Both in-tree sources are auto-discovered.
    assert "huggingface" in names
    assert "lmstudio" in names


def test_sources_have_local_path_and_count(client: TestClient) -> None:
    r = client.get("/v1/sources")
    assert r.status_code == 200
    for s in r.json():
        assert "local_path" in s
        assert s["model_count"] == 0  # empty vault
        assert s["total_bytes"] == 0


def test_source_by_name(client: TestClient) -> None:
    r = client.get("/v1/sources/huggingface")
    assert r.status_code == 200
    assert r.json()["name"] == "huggingface"


def test_source_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/v1/sources/nope")
    assert r.status_code == 404


# --- /v1/services ------------------------------------------------------------


def test_services_list_includes_disabled(client: TestClient) -> None:
    r = client.get("/v1/services")
    assert r.status_code == 200
    body = r.json()
    names = {s["name"] for s in body}
    # Every in-tree service appears, regardless of enabled state (ADR-029).
    assert "llama_swap" in names
    # State is one of the documented values; unavailable services (no binary
    # installed in the test env) report "unavailable" rather than calling
    # into ``status()``.
    for s in body:
        assert s["state"] in {"running", "stopped", "starting", "stopping", "failed", "unavailable"}
        # Endpoint URLs surface on the list so a downstream consumer
        # can build a "what's running and where" view without N+1
        # calls into the detail route.
        assert "runtime_endpoint" in s
        assert "web_ui_endpoint" in s
        # Not running → both endpoints None.
        if s["state"] != "running":
            assert s["runtime_endpoint"] is None
            assert s["web_ui_endpoint"] is None


def test_service_detail(client: TestClient) -> None:
    r = client.get("/v1/services/llama_swap")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "llama_swap"
    assert body["category"] == "llm"
    assert "capabilities" in body
    assert body["capabilities"]["can_serve_llm"] is True
    assert body["is_running"] is False
    assert body["runtime_endpoint"] is None


def test_service_status_subroute(client: TestClient) -> None:
    r = client.get("/v1/services/llama_swap/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] in {"running", "stopped", "starting", "stopping", "failed", "unavailable"}


def test_service_unknown_returns_404(client: TestClient) -> None:
    assert client.get("/v1/services/nope").status_code == 404
    assert client.get("/v1/services/nope/status").status_code == 404


# --- /v1/catalog -------------------------------------------------------------


def test_catalog_empty_vault(client: TestClient) -> None:
    r = client.get("/v1/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == 1
    assert body["entries"] == []
    assert body["generated_at"]


def test_catalog_by_source_empty(client: TestClient) -> None:
    r = client.get("/v1/catalog/by-source")
    assert r.status_code == 200
    body = r.json()
    assert body["by_source"] == {}
    assert body["generated_at"]


def test_catalog_entry_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/v1/catalog/huggingface/no-such-model")
    assert r.status_code == 404


def test_catalog_roundtrip_with_one_model(client: TestClient, tmp_path: Path) -> None:
    """Drop a fake model into the vault, rescan, and confirm the API sees it.

    The rescan path is exercised through the facade, not directly — this
    is the same flow a user would hit on the dashboard's "Rescan" button.
    """
    # Build a minimal HF-style snapshot: <vault>/huggingface/hub/models--org--name/{refs,snapshots}/...
    repo_root = tmp_path / "vault" / "huggingface" / "hub" / "models--acme--demo"
    snapshot_dir = repo_root / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    (repo_root / "refs").mkdir()
    (repo_root / "refs" / "main").write_text("abc123")
    weights = snapshot_dir / "model.safetensors"
    weights.write_bytes(b"\x00" * 4096)

    client.app  # noqa: B018 — touch to ensure lifespan has fired
    deps.get_worker().rescan_catalog()

    r = client.get("/v1/catalog")
    body = r.json()
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["source"] == "huggingface"
    assert entry["name"] == "acme/demo"
    assert entry["total_bytes"] == 4096
    assert any(p["role"] == "main" for p in entry["pieces"])

    r2 = client.get("/v1/catalog/huggingface/acme/demo")
    assert r2.status_code == 200
    assert r2.json()["name"] == "acme/demo"


# --- /v1/sessions ------------------------------------------------------------


def test_sessions_empty(client: TestClient) -> None:
    r = client.get("/v1/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_session_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/v1/sessions/deadbeef")
    assert r.status_code == 404


# --- Module hygiene ----------------------------------------------------------


def test_app_factory_is_idempotent() -> None:
    """``create_app()`` can be called repeatedly without leaking state.

    We exercise routes through the TestClient rather than introspecting
    ``app.routes``: mounted routers in modern FastAPI are wrapped in
    ``_IncludedRouter`` which doesn't expose ``path`` directly.
    """
    a = create_app()
    b = create_app()
    assert a is not b
    # A request against the first doesn't see the second's state because
    # uvicorn would never share state between two ``app`` instances.
    # Just confirm both are wired the same by hitting one canonical route.
    with TestClient(a) as ca, TestClient(b) as cb:
        ra = ca.get("/health")
        rb = cb.get("/health")
        assert ra.status_code == 200
        assert rb.status_code == 200
        assert ra.json() == rb.json()
