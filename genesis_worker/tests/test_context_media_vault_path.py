"""Tests for ADR-037 — ``media_vault_path`` on ``ServiceContext``.

Mirrors ``test_context_vault_path.py`` for the new media vault field.
The media vault is service-only today: ``SourceContext`` does NOT
carry it. The asymmetry is deliberate (ADR-037 §2.1) and locked here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.contracts import ServiceContext, SourceContext
from genesis_worker.contracts.context import PluginContext
from genesis_worker.facade import GenesisWorker
from genesis_worker.registries import ServiceRegistry
from genesis_worker.tests._factories import service_ctx

# --- ServiceContext directly -----------------------------------------------


def test_service_context_carries_media_vault_path(tmp_path: Path) -> None:
    media = tmp_path / "media"
    ctx = service_ctx(tmp_path, media_vault_path=media)
    assert ctx.media_vault_path == media


def test_service_context_media_vault_path_defaults_when_unset(tmp_path: Path) -> None:
    ctx = service_ctx(tmp_path)
    assert ctx.media_vault_path == tmp_path / "media-vault"


# --- SourceContext asymmetry (ADR-037: service-only) -----------------------


def test_source_context_does_not_carry_media_vault_path() -> None:
    """``SourceContext`` deliberately omits ``media_vault_path`` (ADR-037).

    Sources walk acquired content (the model vault); no source walks
    user-produced content today. The field is service-only. If a
    future source needs the media vault, lifting it to
    ``PluginContext`` is a one-line ADR.
    """
    assert "media_vault_path" not in SourceContext.__dataclass_fields__


def test_plugin_context_does_not_carry_media_vault_path() -> None:
    """``media_vault_path`` is on ``ServiceContext`` only, not the shared base."""
    assert "media_vault_path" not in PluginContext.__dataclass_fields__


# --- PluginContext field declaration --------------------------------------


def test_service_context_field_order_media_vault_after_vault_path() -> None:
    """``media_vault_path`` follows ``vault_path`` on ``ServiceContext``.

    Locks the relative field order so an accidental reordering
    surfaces as a test failure rather than as a positional-caller
    breakage. The exact offset isn't load-bearing — ``host_info`` /
    ``secrets`` / ``options`` from the base ``PluginContext`` sit
    between them in the dataclass field list.
    """
    fields = list(ServiceContext.__dataclass_fields__.keys())
    assert fields.index("media_vault_path") > fields.index("vault_path")


# --- Registry population --------------------------------------------------


def test_service_registry_populates_media_vault_path_on_every_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every service plugin's context has ``media_vault_path`` set."""
    from genesis_worker.settings import Settings

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    settings = Settings()
    expected_media = settings.paths.resolved_media_vault_path

    reg = ServiceRegistry(settings)
    for service in reg.all():
        assert service._ctx.media_vault_path == expected_media, (  # noqa: SLF001
            f"{service.name}.ctx.media_vault_path mismatch"
        )


def test_facade_exposes_media_vault_path_through_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke: ``worker.service(...)`` returns an ``InferenceService`` whose
    ``ctx.media_vault_path`` is populated."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    worker = GenesisWorker()
    for svc in worker.services.all():
        ctx = svc._ctx  # noqa: SLF001
        assert ctx.media_vault_path is not None
        assert isinstance(ctx.media_vault_path, Path)


def test_source_registry_does_not_set_media_vault_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source contexts are constructed without a media vault field at all."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    from genesis_worker.registries import SourceRegistry
    from genesis_worker.settings import Settings

    settings = Settings()
    reg = SourceRegistry(settings)
    for source in reg.all():
        ctx = source._ctx  # noqa: SLF001
        assert isinstance(ctx, SourceContext)
        assert "media_vault_path" not in ctx.__dataclass_fields__


def test_factory_kwargs_reach_context(tmp_path: Path) -> None:
    """The factory kwargs reach the constructed context unchanged."""
    media = tmp_path / "custom-media"
    ctx = service_ctx(tmp_path, media_vault_path=media)
    assert ctx.media_vault_path == media
    # Vault path still defaults independently — fields are independent.
    assert ctx.vault_path == tmp_path / "vault"
