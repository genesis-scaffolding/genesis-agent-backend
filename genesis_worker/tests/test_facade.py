"""Tests for the GenesisWorker facade."""

from __future__ import annotations

from pathlib import Path

from genesis_worker import GenesisWorker
from genesis_worker.contracts import (
    ServiceCapabilities,
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)
from genesis_worker.utils.models import MachineMetrics


def test_facade_lists_services(tmp_path: Path) -> None:
    w = GenesisWorker()
    services = w.list_services()
    assert any(s.name == "llama_swap" for s in services)


def test_list_enabled_services_filters_by_registry_state(tmp_path: Path, monkeypatch) -> None:
    """list_enabled_services() must only return services the registry has enabled.

    Hermetic: uses a tmp state_dir so we don't pollute user state, and
    mocks ``is_available`` so the bootstrap doesn't auto-enable things
    behind our back.
    """
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.services.llama_swap import LlamaSwapService
    from genesis_worker.services.sillytavern import SillyTavernService
    from genesis_worker.settings import PathsSettings, Settings

    monkeypatch.setattr(LlamaSwapService, "is_available", lambda self: False)
    monkeypatch.setattr(SillyTavernService, "is_available", lambda self: False)

    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )
    w = _GW(settings=settings)
    w.services.enable("llama_swap")

    enabled_names = {s.name for s in w.list_enabled_services()}
    all_names = {s.name for s in w.list_services()}
    assert "llama_swap" in enabled_names
    assert "sillytavern" not in enabled_names
    # list_services still returns everything; only list_enabled_services filters.
    assert all_names.issuperset(enabled_names)


def test_service_info_carries_category_and_description(tmp_path: Path) -> None:
    """ServiceInfo gains category + description fields (ADR-029).

    Hermetic test against tmp state_dir.
    """
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import ServiceCategory
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    w = _GW(settings=settings)

    llama = next(s for s in w.list_services() if s.name == "llama_swap")
    assert llama.category == ServiceCategory.LLM
    assert llama.description == "OpenAI-compatible LLM server"


def test_facade_returns_service_instance(tmp_path: Path) -> None:
    w = GenesisWorker()
    svc = w.service("llama_swap")
    assert svc.capabilities().can_serve_llm


def test_facade_service_status(tmp_path: Path) -> None:
    w = GenesisWorker()
    status = w.service_status("llama_swap")
    assert isinstance(status, ServiceStatus)
    assert status.state in (ServiceState.RUNNING, ServiceState.STOPPED, ServiceState.FAILED)


def test_facade_start_service_returns_start_result(tmp_path: Path) -> None:
    """Calling start when already running returns a result; we don't actually start."""
    w = GenesisWorker()
    result = w.start_service("llama_swap")
    assert isinstance(result, StartResult)


def test_facade_stop_service_returns_stop_result(tmp_path: Path) -> None:
    w = GenesisWorker()
    result = w.stop_service("llama_swap")
    assert isinstance(result, StopResult)


def test_facade_collect_metrics(tmp_path: Path) -> None:
    w = GenesisWorker()
    m = w.collect_metrics()
    assert isinstance(m, MachineMetrics)


def test_ui_pages_property_exists_with_concrete_default(tmp_path: Path) -> None:
    """Default ui_pages returns an empty list. Plugins override."""
    w = GenesisWorker()
    svc = w.service("llama_swap")
    assert isinstance(svc.ui_pages, list)
    assert all(isinstance(p, UiPage) for p in svc.ui_pages)


def test_service_capabilities_distinguishes_web_ui(tmp_path: Path) -> None:
    """has_web_ui means the service's own web UI on its native port."""
    w = GenesisWorker()
    caps = w.service("llama_swap").capabilities()
    assert isinstance(caps, ServiceCapabilities)
    # The contract documents that has_web_ui is about the service's own web UI
    # (e.g. llama-swap's :8080), not worker-managed Streamlit pages.
    assert caps.has_web_ui is True


def test_facade_catalog_persists_across_instances(tmp_path: Path, monkeypatch) -> None:
    """A catalog written by one worker survives across worker restarts."""
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.settings import PathsSettings, Settings

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(paths=PathsSettings(state_dir=state_dir))
    w1 = _GW(settings=settings)
    first = w1.rescan_catalog()
    assert (state_dir / "catalog.json").is_file()

    w2 = _GW(settings=settings)
    loaded = w2.catalog()
    assert loaded.generated_at == first.generated_at
    assert loaded.content_hash == first.content_hash


def test_delete_model_removes_entry_and_directory(tmp_path: Path) -> None:
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
    from genesis_worker.settings import PathsSettings, Settings
    from genesis_worker.utils.catalog_utils import compute_content_hash

    vault = tmp_path / "vault"
    model_dir = vault / "org" / "repo"
    model_dir.mkdir(parents=True)
    (model_dir / "model.gguf").write_text("weights")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(
        paths=PathsSettings(state_dir=state_dir, vault_path=vault),
    )

    entry = ModelEntry(
        name="org/repo",
        source="huggingface",
        pieces=[
            ModelPiece(role="main", filename="model.gguf", path=model_dir / "model.gguf", bytes=7)
        ],
        total_bytes=7,
        directory=str(model_dir),
    )
    catalog = Catalog(
        root=str(vault),
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash=compute_content_hash([entry]),
        entries=[entry],
    )
    import json

    (state_dir / "catalog.json").write_text(catalog.model_dump_json())

    w = _GW(settings=settings)
    # prime the cache
    _ = w.catalog()

    w.delete_model("huggingface", "org/repo")

    assert not model_dir.exists()
    assert not any(e.name == "org/repo" for e in w.catalog().entries)
    loaded = json.loads((state_dir / "catalog.json").read_text())
    assert len(loaded["entries"]) == 0


def test_delete_model_removes_entry_when_directory_already_gone(tmp_path: Path) -> None:
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
    from genesis_worker.settings import PathsSettings, Settings
    from genesis_worker.utils.catalog_utils import compute_content_hash

    vault = tmp_path / "vault"
    model_dir = vault / "org" / "repo"
    model_dir.mkdir(parents=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(
        paths=PathsSettings(state_dir=state_dir, vault_path=vault),
    )

    entry = ModelEntry(
        name="org/repo",
        source="huggingface",
        pieces=[
            ModelPiece(role="main", filename="model.gguf", path=model_dir / "model.gguf", bytes=0)
        ],
        total_bytes=0,
        directory=str(model_dir),
    )
    catalog = Catalog(
        root=str(vault),
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash=compute_content_hash([entry]),
        entries=[entry],
    )
    (state_dir / "catalog.json").write_text(catalog.model_dump_json())

    w = _GW(settings=settings)
    _ = w.catalog()

    w.delete_model("huggingface", "org/repo")

    assert not any(e.name == "org/repo" for e in w.catalog().entries)


def test_delete_model_raises_for_unknown_entry(tmp_path: Path) -> None:
    from genesis_worker import GenesisWorker as _GW
    from genesis_worker.contracts import Catalog, ModelEntry, ModelPiece
    from genesis_worker.settings import PathsSettings, Settings
    from genesis_worker.utils.catalog_utils import compute_content_hash

    vault = tmp_path / "vault"
    model_dir = vault / "other" / "repo"
    model_dir.mkdir(parents=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(
        paths=PathsSettings(state_dir=state_dir, vault_path=vault),
    )

    entry = ModelEntry(
        name="other/repo",
        source="huggingface",
        pieces=[
            ModelPiece(role="main", filename="model.gguf", path=model_dir / "model.gguf", bytes=0)
        ],
        total_bytes=0,
        directory=str(model_dir),
    )
    catalog = Catalog(
        root=str(vault),
        generated_at="2026-01-01T00:00:00+00:00",
        content_hash=compute_content_hash([entry]),
        entries=[entry],
    )
    (state_dir / "catalog.json").write_text(catalog.model_dump_json())

    w = _GW(settings=settings)
    _ = w.catalog()

    import pytest

    with pytest.raises(ValueError, match="No entry found"):
        w.delete_model("huggingface", "nonexistent/repo")
