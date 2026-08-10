"""Tests for the top-level GenesisWorker facade."""

from __future__ import annotations

from pathlib import Path

from genesis_worker import GenesisWorker, ServiceInfo, SourceInfo
from genesis_worker.settings import PathsSettings, Settings

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_construction() -> None:
    """``GenesisWorker()`` with no args builds default Settings and discovers both axes."""
    w = GenesisWorker()
    assert isinstance(w.settings, Settings)
    assert {src.name for src in w.sources.all()} == {"huggingface", "lmstudio"}
    assert {svc.name for svc in w.services.all()} == {"llama_swap"}


def test_construction_with_explicit_settings() -> None:
    """Passing a pre-built Settings wires it through."""
    settings = Settings(paths=PathsSettings(vault_path=Path("/custom")))
    w = GenesisWorker(settings=settings)
    assert w.settings is settings
    assert w.sources.vault_path == Path("/custom")


def test_construction_with_empty_vault(tmp_path: Path) -> None:
    """Construction works even when the vault has no models yet."""
    w = GenesisWorker(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = w.rescan_catalog()
    assert cat.huggingface == []
    assert cat.lmstudio == []


# ---------------------------------------------------------------------------
# Registries exposed
# ---------------------------------------------------------------------------


def test_sources_property_returns_registry() -> None:
    """``worker.sources`` is the live SourceRegistry instance."""
    w = GenesisWorker()
    assert {src.name for src in w.sources.all()} == {"huggingface", "lmstudio"}
    # Idempotent — same registry on repeated access.
    assert w.sources is w.sources


def test_services_property_returns_registry() -> None:
    """``worker.services`` is the live ServiceRegistry instance."""
    w = GenesisWorker()
    assert {svc.name for svc in w.services.all()} == {"llama_swap"}
    assert w.services is w.services


def test_catalog_service_property() -> None:
    """``worker.catalog_service`` is the live CatalogService."""
    w = GenesisWorker()
    # Calling rescan through either path produces equivalent results.
    cat_a = w.catalog_service.rescan()
    cat_b = w.rescan_catalog()
    assert len(cat_a.huggingface) == len(cat_b.huggingface)
    assert len(cat_a.lmstudio) == len(cat_b.lmstudio)


# ---------------------------------------------------------------------------
# Catalog access
# ---------------------------------------------------------------------------


def test_rescan_catalog_returns_catalog(tmp_path: Path) -> None:
    """``rescan_catalog()`` returns a fresh Catalog each time."""
    # Build a fake vault so the rescan finds something.
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--demo"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.gguf").write_bytes(b"\x00" * 100)
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc")

    w = GenesisWorker(Settings(paths=PathsSettings(vault_path=tmp_path)))
    cat = w.rescan_catalog()
    assert len(cat.huggingface) == 1
    assert cat.huggingface[0].name == "acme/demo"


def test_catalog_caches_until_rescan(tmp_path: Path) -> None:
    """``catalog()`` returns the cached result; ``rescan_catalog()`` invalidates it."""
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--demo"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.gguf").write_bytes(b"\x00" * 100)
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc")

    w = GenesisWorker(Settings(paths=PathsSettings(vault_path=tmp_path)))
    first = w.catalog()
    second = w.catalog()
    # Same cached instance — no re-walk.
    assert first is second

    # Adding a new repo invalidates the cache after rescan.
    new_repo = hub / "models--acme--second"
    new_snapshot = new_repo / "snapshots" / "def"
    new_snapshot.mkdir(parents=True)
    (new_snapshot / "model.gguf").write_bytes(b"\x00" * 50)
    (new_repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (new_repo / "refs" / "main").write_text("def")

    fresh = w.rescan_catalog()
    assert fresh is not first
    assert len(fresh.huggingface) == 2


# ---------------------------------------------------------------------------
# Inspection helpers (used by UI / CLI)
# ---------------------------------------------------------------------------


def test_list_sources_returns_source_info() -> None:
    """``list_sources()`` returns display-friendly SourceInfo records."""
    w = GenesisWorker()
    sources = w.list_sources()
    assert len(sources) == 2
    assert all(isinstance(s, SourceInfo) for s in sources)
    by_name = {s.name: s for s in sources}
    assert by_name["huggingface"].display_name == "HuggingFace"
    assert by_name["huggingface"].can_acquire is True
    assert by_name["lmstudio"].can_acquire is False


def test_list_services_returns_service_info() -> None:
    """``list_services()`` returns display-friendly ServiceInfo records."""
    w = GenesisWorker()
    services = w.list_services()
    assert len(services) == 1
    assert all(isinstance(s, ServiceInfo) for s in services)
    svc = services[0]
    assert svc.name == "llama_swap"
    assert svc.display_name == "llama-swap"
    assert svc.capabilities.can_serve_llm


# ---------------------------------------------------------------------------
# Convenience use case: a CLI listing in one expression
# ---------------------------------------------------------------------------


def test_one_liner_lists_services() -> None:
    """The pattern used by spec-003 verification step 2 works."""
    assert any(
        info.name == "llama_swap"
        for info in GenesisWorker().list_services()
    )
