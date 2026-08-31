"""Tests for ADR-023 — `vault_path` on PluginContext, inherited by both contexts."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.facade import GenesisWorker
from genesis_worker.registries import ServiceRegistry, SourceRegistry
from genesis_worker.tests._factories import service_ctx, source_ctx

# --- PluginContext directly -----------------------------------------------


def test_plugin_context_carries_vault_path(tmp_path: Path) -> None:
    """ServiceContext (which is just PluginContext) exposes vault_path."""
    vault = tmp_path / "vault"
    ctx = service_ctx(tmp_path, vault_path=vault)
    assert ctx.vault_path == vault


def test_source_context_inherits_vault_path(tmp_path: Path) -> None:
    """SourceContext inherits vault_path from PluginContext."""
    vault = tmp_path / "vault"
    ctx = source_ctx(tmp_path, vault_path=vault)
    assert ctx.vault_path == vault


def test_service_context_vault_path_defaults_when_unset(tmp_path: Path) -> None:
    """factory defaults vault_path to <root>/vault when not supplied."""
    ctx = service_ctx(tmp_path)
    assert ctx.vault_path == tmp_path / "vault"


def test_source_context_vault_path_defaults_when_unset(tmp_path: Path) -> None:
    """factory defaults vault_path to <root>/vault when not supplied."""
    ctx = source_ctx(tmp_path)
    assert ctx.vault_path == tmp_path / "vault"


# --- PluginContext field declaration --------------------------------------


def test_plugin_context_field_order_has_vault_path_after_repo_root() -> None:
    """vault_path is declared on PluginContext, between repo_root and secrets.

    Locks the field order so an accidental reordering in the future
    surfaces as a test failure rather than as a positional-caller
    breakage. The order matches ADR-023 Decision: Contract change.
    """
    from genesis_worker.contracts.context import PluginContext

    fields = list(PluginContext.__dataclass_fields__.keys())
    assert fields.index("vault_path") == fields.index("repo_root") + 1
    assert fields.index("secrets") == fields.index("vault_path") + 1


# --- Registry population --------------------------------------------------


def test_source_registry_populates_vault_path_on_every_source(tmp_path: Path, monkeypatch) -> None:
    """Every source plugin's context has vault_path = settings.paths.resolved_vault_path."""
    from genesis_worker.settings import Settings

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    settings = Settings()
    expected_vault = settings.paths.resolved_vault_path

    reg = SourceRegistry(settings)
    for source in reg.all():
        assert source._ctx.vault_path == expected_vault, (  # noqa: SLF001
            f"{source.name}.ctx.vault_path mismatch"
        )


def test_service_registry_populates_vault_path_on_every_service(
    tmp_path: Path, monkeypatch
) -> None:
    """Every service plugin's context has vault_path = settings.paths.resolved_vault_path."""
    from genesis_worker.settings import Settings

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    settings = Settings()
    expected_vault = settings.paths.resolved_vault_path

    reg = ServiceRegistry(settings)
    for service in reg.all():
        assert service._ctx.vault_path == expected_vault, (  # noqa: SLF001
            f"{service.name}.ctx.vault_path mismatch"
        )


def test_facade_exposes_vault_path_through_services(tmp_path: Path, monkeypatch) -> None:
    """Smoke: worker.service(...) returns an InferenceService whose ctx.vault_path is set."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    worker = GenesisWorker()
    for svc in worker.services.all():
        ctx = svc._ctx  # noqa: SLF001
        # vault_path should be set to a non-empty path (resolved_vault_path)
        assert ctx.vault_path is not None
        assert isinstance(ctx.vault_path, Path)
