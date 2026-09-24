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


@pytest.mark.integration
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


@pytest.mark.integration
def test_service_status_subroute(client: TestClient) -> None:
    r = client.get("/v1/services/llama_swap/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] in {"running", "stopped", "starting", "stopping", "failed", "unavailable"}


def test_service_unknown_returns_404(client: TestClient) -> None:
    assert client.get("/v1/services/nope").status_code == 404
    assert client.get("/v1/services/nope/status").status_code == 404


# --- /v1/services POST endpoints (ADR-038) ---------------------------------


def _stub_install_response() -> dict:
    """Canonical install payload the route layer returns on success."""
    return {"installed": True, "version": "v1"}


def test_post_install_happy_path(client: TestClient, monkeypatch) -> None:
    """POST /install returns 200 + the install payload on success."""
    from genesis_worker import GenesisWorker

    monkeypatch.setattr(
        GenesisWorker,
        "install_service",
        lambda self, name: _stub_install_response(),
    )
    r = client.post("/v1/services/llama_swap/install")
    assert r.status_code == 200
    assert r.json() == {"installed": True, "version": "v1"}


def test_post_install_404_unknown_service(client: TestClient, monkeypatch) -> None:
    """Unknown service name → 404 (facade raises KeyError → route translates)."""
    from genesis_worker import GenesisWorker

    def _raise(self, name):
        raise KeyError(name)

    monkeypatch.setattr(GenesisWorker, "install_service", _raise)
    r = client.post("/v1/services/nope/install")
    assert r.status_code == 404
    assert "unknown service" in r.json()["detail"]


def test_post_install_409_capability_refused(client: TestClient, monkeypatch) -> None:
    """ServiceCapabilityError → 409 Conflict."""
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import ServiceCapabilityError

    def _raise(self, name):
        raise ServiceCapabilityError("service 'x' cannot be installed")

    monkeypatch.setattr(GenesisWorker, "install_service", _raise)
    r = client.post("/v1/services/x/install")
    assert r.status_code == 409
    assert "cannot be installed" in r.json()["detail"]


def test_post_install_503_when_in_progress(client: TestClient, monkeypatch) -> None:
    """InstallInProgressError → 503 Service Unavailable."""
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import InstallInProgressError

    def _raise(self, name):
        raise InstallInProgressError("install already in progress for 'x'")

    monkeypatch.setattr(GenesisWorker, "install_service", _raise)
    r = client.post("/v1/services/x/install")
    assert r.status_code == 503
    assert "already in progress" in r.json()["detail"]


def test_post_install_idempotent_re_call(client: TestClient, monkeypatch) -> None:
    """A second call returns the same shape — the facade is idempotent."""
    from genesis_worker import GenesisWorker

    monkeypatch.setattr(
        GenesisWorker, "install_service", lambda self, name: _stub_install_response()
    )
    r1 = client.post("/v1/services/llama_swap/install")
    r2 = client.post("/v1/services/llama_swap/install")
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json() == {"installed": True, "version": "v1"}


def test_post_start_happy_path_with_no_body(client: TestClient, monkeypatch) -> None:
    """Empty body (no config) → 200 + StartResult-shaped body."""
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import StartResult

    captured: dict = {}

    def _stub(self, name, *, config=None):
        captured["name"] = name
        captured["config"] = config
        return StartResult(ok=True, message="started", pid=4242)

    monkeypatch.setattr(GenesisWorker, "start_service", _stub)
    r = client.post("/v1/services/llama_swap/start")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "started", "pid": 4242}
    assert captured == {"name": "llama_swap", "config": None}


def test_post_start_with_config_passes_through_to_facade(client: TestClient, monkeypatch) -> None:
    """Body ``{"config": {...}}`` is forwarded to ``start_service(name, config=...)``."""
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import StartResult

    captured: dict = {}

    def _stub(self, name, *, config=None):
        captured["name"] = name
        captured["config"] = config
        return StartResult(ok=True, message="started")

    monkeypatch.setattr(GenesisWorker, "start_service", _stub)
    body = {"config": {"providers": {"openai": {"keys": [{"name": "k"}]}}}}
    r = client.post("/v1/services/bifrost/start", json=body)
    assert r.status_code == 200
    assert captured["name"] == "bifrost"
    assert captured["config"] == body["config"]


def test_post_start_404_unknown_service(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker

    def _raise(self, name, *, config=None):
        raise KeyError(name)

    monkeypatch.setattr(GenesisWorker, "start_service", _raise)
    r = client.post("/v1/services/nope/start")
    assert r.status_code == 404


def test_post_start_500_when_facade_raises_runtime(client: TestClient, monkeypatch) -> None:
    """A start failure after materialisation → 500 with the message in ``detail``.

    Per the orchestrator's contract: 500 means the on-disk config has
    been written and the service is stopped. The caller surfaces
    ``detail`` and does not retry blindly.
    """
    from genesis_worker import GenesisWorker

    def _raise(self, name, *, config=None):
        raise RuntimeError("container exited before health probe")

    monkeypatch.setattr(GenesisWorker, "start_service", _raise)
    r = client.post("/v1/services/bifrost/start", json={"config": {"x": 1}})
    assert r.status_code == 500
    assert "container exited" in r.json()["detail"]


def test_post_start_409_capability_refused(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import ServiceCapabilityError

    def _raise(self, name, *, config=None):
        raise ServiceCapabilityError("binary missing")

    monkeypatch.setattr(GenesisWorker, "start_service", _raise)
    r = client.post("/v1/services/x/start")
    assert r.status_code == 409
    assert "binary missing" in r.json()["detail"]


def test_post_stop_happy_path(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import StopResult

    monkeypatch.setattr(
        GenesisWorker, "stop_service", lambda self, name: StopResult(ok=True, message="stopped")
    )
    r = client.post("/v1/services/llama_swap/stop")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "stopped"}


def test_post_stop_idempotent_when_not_running(client: TestClient, monkeypatch) -> None:
    """Stop on a non-running service is a no-op success (facade returns ok=True)."""
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import StopResult

    monkeypatch.setattr(
        GenesisWorker,
        "stop_service",
        lambda self, name: StopResult(ok=True, message="not running"),
    )
    r = client.post("/v1/services/llama_swap/stop")
    assert r.status_code == 200
    assert "not running" in r.json()["message"]


def test_post_stop_404_unknown_service(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker

    def _raise(self, name):
        raise KeyError(name)

    monkeypatch.setattr(GenesisWorker, "stop_service", _raise)
    r = client.post("/v1/services/nope/stop")
    assert r.status_code == 404


def test_post_restart_happy_path(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import StartResult

    captured: dict = {}

    def _stub(self, name, *, config=None):
        captured["name"] = name
        captured["config"] = config
        return StartResult(ok=True, message="restarted", pid=1234)

    monkeypatch.setattr(GenesisWorker, "restart_service", _stub)
    r = client.post("/v1/services/llama_swap/restart")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "restarted", "pid": 1234}
    assert captured == {"name": "llama_swap", "config": None}


def test_post_restart_with_config_passes_through(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker
    from genesis_worker.contracts import StartResult

    captured: dict = {}

    def _stub(self, name, *, config=None):
        captured["name"] = name
        captured["config"] = config
        return StartResult(ok=True, message="restarted")

    monkeypatch.setattr(GenesisWorker, "restart_service", _stub)
    body = {"config": {"providers": {"yoga": {"keys": []}}}}
    r = client.post("/v1/services/bifrost/restart", json=body)
    assert r.status_code == 200
    assert captured == {"name": "bifrost", "config": body["config"]}


def test_post_restart_500_when_facade_raises_runtime(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker

    def _raise(self, name, *, config=None):
        raise RuntimeError("restart failed")

    monkeypatch.setattr(GenesisWorker, "restart_service", _raise)
    r = client.post("/v1/services/x/restart")
    assert r.status_code == 500
    assert "restart failed" in r.json()["detail"]


def test_post_restart_404_unknown_service(client: TestClient, monkeypatch) -> None:
    from genesis_worker import GenesisWorker

    def _raise(self, name, *, config=None):
        raise KeyError(name)

    monkeypatch.setattr(GenesisWorker, "restart_service", _raise)
    r = client.post("/v1/services/nope/restart")
    assert r.status_code == 404


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
