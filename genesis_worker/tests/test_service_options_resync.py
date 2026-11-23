"""A plain restart re-reads the service's options sidecar (ADR-053).

The bug this file pins down: editing
``<config_dir>/services/<name>.overrides.json`` by hand and then issuing
``POST /v1/services/<name>/restart`` with **no body** was a silent no-op
— the container came back with the options the worker happened to load at
boot.

The sidecar is the authority for a service's options; the in-memory
instance is a cache of it. Every start re-reads the disk and rebuilds the
instance when the two disagree.

Hermetic: every path is rooted in ``tmp_path`` and the docker run is
stubbed, so no daemon is touched.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from genesis_worker import GenesisWorker
from genesis_worker.api import deps
from genesis_worker.api.app import create_app
from genesis_worker.contracts import StartResult
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.utils.process.docker import DockerContainer
from genesis_worker.utils.services.docker_service import DockerService

_SERVICE = "speaches"
_PYTHON_SERVICE = "llama_swap"


# --- fixtures ---------------------------------------------------------------


def _worker(tmp_path: Path) -> GenesisWorker:
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
    return GenesisWorker(settings=settings)


def _sidecar(tmp_path: Path, name: str = _SERVICE) -> Path:
    return tmp_path / "config" / "services" / f"{name}.overrides.json"


def _write_sidecar(tmp_path: Path, values: dict[str, Any], name: str = _SERVICE) -> Path:
    path = _sidecar(tmp_path, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))
    return path


def _stub_docker_run(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    """Capture the container env instead of starting a container."""
    monkeypatch.setattr(DockerService, "is_available", lambda self: True)

    def _run(self: DockerContainer, **kwargs: Any) -> StartResult:
        captured.update(kwargs)
        return StartResult(ok=True, message="stubbed")

    monkeypatch.setattr(DockerContainer, "run", _run)


@pytest.fixture
def worker(tmp_path: Path) -> GenesisWorker:
    return _worker(tmp_path)


@pytest.fixture
def client(worker: GenesisWorker) -> Generator[TestClient, None, None]:
    deps._worker = worker
    with TestClient(create_app()) as c:
        yield c
    deps._worker = None


# --- the proof test ---------------------------------------------------------


def test_restart_after_direct_sidecar_edit_applies_the_new_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct sidecar edit + a plain restart changes the container's env.

    This is the reported bug: the sidecar is written by hand (as an
    operator would), the restart carries no body, and the env the
    container is started with must reflect the edit.
    """
    worker = _worker(tmp_path)
    captured: dict[str, Any] = {}
    _stub_docker_run(monkeypatch, captured)

    _write_sidecar(tmp_path, {"extra_env": {"DIRECT_EDIT": "1"}})

    result = worker.restart_service(_SERVICE)

    assert result.ok
    assert captured["env"]["DIRECT_EDIT"] == "1"


def test_post_restart_without_body_applies_a_direct_sidecar_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """The same through the HTTP surface the operator actually uses."""
    captured: dict[str, Any] = {}
    _stub_docker_run(monkeypatch, captured)

    _write_sidecar(tmp_path, {"extra_env": {"DIRECT_EDIT": "1"}})

    r = client.post(f"/v1/services/{_SERVICE}/restart")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert captured["env"]["DIRECT_EDIT"] == "1"


def test_start_after_direct_sidecar_edit_applies_the_new_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plain Start path converges too — same code path as the restart."""
    worker = _worker(tmp_path)
    captured: dict[str, Any] = {}
    _stub_docker_run(monkeypatch, captured)

    _write_sidecar(tmp_path, {"extra_env": {"DIRECT_EDIT": "1"}})

    result = worker.start_service(_SERVICE)

    assert result.ok
    assert captured["env"]["DIRECT_EDIT"] == "1"


# --- the instance is only replaced when something changed -------------------


def test_restart_without_sidecar_change_keeps_the_service_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No drift → no rebuild → the instance keeps its identity."""
    worker = _worker(tmp_path)
    captured: dict[str, Any] = {}
    _stub_docker_run(monkeypatch, captured)

    before = worker.service(_SERVICE)

    result = worker.restart_service(_SERVICE)

    assert result.ok
    assert worker.service(_SERVICE) is before


def test_restart_after_sidecar_removal_drops_the_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the sidecar reverts the option to the spec default."""
    worker = _worker(tmp_path)
    captured: dict[str, Any] = {}
    _stub_docker_run(monkeypatch, captured)

    _write_sidecar(tmp_path, {"extra_env": {"DIRECT_EDIT": "1"}})
    worker.restart_service(_SERVICE)
    assert captured["env"]["DIRECT_EDIT"] == "1"

    _sidecar(tmp_path).unlink()
    worker.restart_service(_SERVICE)

    assert "DIRECT_EDIT" not in captured["env"]


# --- the orchestrator path is untouched (ADR-046) ---------------------------


def test_restart_with_an_options_body_still_pushes_the_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body with an ``options`` section still writes the sidecar and rebuilds."""
    worker = _worker(tmp_path)
    captured: dict[str, Any] = {}
    _stub_docker_run(monkeypatch, captured)

    result = worker.restart_service(_SERVICE, config={"options": {"extra_env": {"PUSHED": "yes"}}})

    assert result.ok
    assert captured["env"]["PUSHED"] == "yes"
    assert "PUSHED" in _sidecar(tmp_path).read_text()


def test_orchestrator_options_on_a_python_service_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-046: applying options to a Python service raises."""
    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "start", lambda self: StartResult(ok=True, message="m"))
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: True)
    worker = _worker(tmp_path)

    with pytest.raises(RuntimeError, match="only declarative services"):
        worker.start_service(_PYTHON_SERVICE, config={"options": {"x": 1}})


# --- Python services and corrupt sidecars -----------------------------------


def test_restart_of_a_python_service_with_a_drifted_sidecar_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Python service has no declarative rebuild path — it is left alone."""
    from genesis_worker.services.llama_swap import LlamaSwapService

    monkeypatch.setattr(LlamaSwapService, "start", lambda self: StartResult(ok=True, message="m"))
    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: True)
    worker = _worker(tmp_path)
    before = worker.service(_PYTHON_SERVICE)

    _write_sidecar(tmp_path, {"extra_env": {"DIRECT_EDIT": "1"}}, name=_PYTHON_SERVICE)

    result = worker.restart_service(_PYTHON_SERVICE)

    assert result.ok
    assert worker.service(_PYTHON_SERVICE) is before


def test_restart_with_a_malformed_sidecar_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt sidecar must not be a silent no-op — it fails with the path."""
    worker = _worker(tmp_path)
    captured: dict[str, Any] = {}
    _stub_docker_run(monkeypatch, captured)

    path = _sidecar(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"extra_env": ')

    with pytest.raises(RuntimeError) as excinfo:
        worker.restart_service(_SERVICE)

    assert "malformed service overrides" in str(excinfo.value)
    assert str(path) in str(excinfo.value)
