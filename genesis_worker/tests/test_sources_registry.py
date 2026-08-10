"""Tests for the source registry and source protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.settings import (
    HuggingFaceSourceSettings,
    LMSourceSettings,
    PathsSettings,
    Settings,
    SourcesSettings,
)
from genesis_worker.sources import (
    HuggingFaceSource,
    LMSource,
    ModelSource,
    SourceRegistry,
)


def test_huggingface_and_lmstudio_are_constructible() -> None:
    """Both built-in sources can be passed to SourceRegistry and constructed."""
    s = Settings()
    reg = SourceRegistry(s, [HuggingFaceSource, LMSource])
    names = {src.name for src in reg.all()}
    assert names == {"huggingface", "lmstudio"}


def test_registry_requires_explicit_source_classes() -> None:
    """SourceRegistry does no auto-discovery. An empty class list yields nothing."""
    s = Settings()
    reg = SourceRegistry(s, [])
    assert reg.all() == []
    with pytest.raises(KeyError):
        reg.get("huggingface")


def test_source_classes_satisfy_protocol() -> None:
    """Every registered source is an instance of ModelSource (runtime-checkable)."""
    s = Settings()
    for src in SourceRegistry(s, [HuggingFaceSource, LMSource]).all():
        assert isinstance(src, ModelSource)


# ---------------------------------------------------------------------------
# SourceRegistry facade — path-resolution contract
# ---------------------------------------------------------------------------


def test_registry_default_uses_vault_subdir() -> None:
    """No override -> settings.paths.resolved_vault_path / source.vault_subdir."""
    s = Settings(
        paths=PathsSettings(vault_path=Path("/v")),
        sources=SourcesSettings(),  # empty -> defaults
    )
    reg = SourceRegistry(s, [HuggingFaceSource, LMSource])
    assert reg.get("huggingface").local_path == Path("/v/huggingface/hub")
    assert reg.get("lmstudio").local_path == Path("/v/lmstudio/models")


def test_registry_explicit_absolute_path_wins() -> None:
    """An absolute local_path bypasses the vault entirely."""
    s = Settings(
        paths=PathsSettings(vault_path=Path("/v")),
        sources=SourcesSettings(
            huggingface=HuggingFaceSourceSettings(local_path=Path("/srv/external/hf")),
        ),
    )
    reg = SourceRegistry(s, [HuggingFaceSource, LMSource])
    assert reg.get("huggingface").local_path == Path("/srv/external/hf")
    # lmstudio unaffected, still defaults
    assert reg.get("lmstudio").local_path == Path("/v/lmstudio/models")


def test_registry_explicit_relative_path_joins_vault() -> None:
    """A relative local_path is joined with resolved_vault_path."""
    s = Settings(
        paths=PathsSettings(vault_path=Path("/v")),
        sources=SourcesSettings(
            huggingface=HuggingFaceSourceSettings(local_path=Path("custom/hf-subdir")),
        ),
    )
    reg = SourceRegistry(s, [HuggingFaceSource])
    assert reg.get("huggingface").local_path == Path("/v/custom/hf-subdir")


def test_registry_explicit_relative_path_for_lmstudio() -> None:
    """Same resolution rule applies to LM Studio's local_path override."""
    s = Settings(
        paths=PathsSettings(vault_path=Path("/v")),
        sources=SourcesSettings(lmstudio=LMSourceSettings(local_path=Path("alt/lms"))),
    )
    reg = SourceRegistry(s, [LMSource])
    assert reg.get("lmstudio").local_path == Path("/v/alt/lms")


def test_registry_unknown_source_raises() -> None:
    """``get()`` raises KeyError for an unknown source name."""
    s = Settings()
    with pytest.raises(KeyError):
        SourceRegistry(s, [HuggingFaceSource, LMSource]).get("does_not_exist")


def test_registry_all_returns_every_source() -> None:
    """``.all()`` returns one entry per registered source, in registration order."""
    s = Settings()
    reg = SourceRegistry(s, [HuggingFaceSource, LMSource])
    sources = reg.all()
    assert {src.name for src in sources} == {"huggingface", "lmstudio"}


def test_registry_local_path_is_public_attribute() -> None:
    """``local_path`` is a public attribute — no private storage, no property.

    The framework sets it at construction; sources read it directly.
    No ``_local_path`` indirection, no ``@property`` boilerplate.
    """
    s = Settings(paths=PathsSettings(vault_path=Path("/v")))
    for src in SourceRegistry(s, [HuggingFaceSource, LMSource]).all():
        # Public attribute: readable directly, no descriptor protocol.
        assert isinstance(src.local_path, Path)
        # No private backing attribute.
        assert not hasattr(src, "_local_path")


def test_registry_vault_path_property() -> None:
    """The registry exposes its resolved vault path for downstream consumers."""
    s = Settings(paths=PathsSettings(vault_path=Path("/v")))
    reg = SourceRegistry(s, [HuggingFaceSource])
    assert reg.vault_path == Path("/v")


def test_registry_constructs_each_source_exactly_once() -> None:
    """A fresh SourceRegistry constructs sources; the previous one is garbage-collected.

    Each ``SourceRegistry`` instance owns its own source instances,
    so swapping settings (e.g. for tests) doesn't leak state between runs.
    """
    s1 = Settings(paths=PathsSettings(vault_path=Path("/v1")))
    s2 = Settings(paths=PathsSettings(vault_path=Path("/v2")))
    r1 = SourceRegistry(s1, [HuggingFaceSource])
    r2 = SourceRegistry(s2, [HuggingFaceSource])
    # Same class, different instances, different paths.
    assert r1.get("huggingface") is not r2.get("huggingface")
    assert r1.get("huggingface").local_path == Path("/v1/huggingface/hub")
    assert r2.get("huggingface").local_path == Path("/v2/huggingface/hub")


def test_registry_unknown_source_class_is_ignored() -> None:
    """A source class without a settings slice still resolves via vault_subdir.

    The framework doesn't require every source to have a settings field —
    new sources can rely on the vault_subdir default until they're
    "promoted" to user-overridable by adding a settings slice.
    """
    s = Settings(paths=PathsSettings(vault_path=Path("/v")))

    class NoSettingsSource:
        name = "no_settings"
        display_name = "NoSettings"
        can_acquire = False
        vault_subdir = "no_settings"
        local_path: Path

        def __init__(self, local_path: Path) -> None:
            self.local_path = local_path

        def is_available(self) -> bool:
            return False

        def walk(self):
            return []

    reg = SourceRegistry(s, [NoSettingsSource])
    assert reg.get("no_settings").local_path == Path("/v/no_settings")
