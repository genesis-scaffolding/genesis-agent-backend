"""Tests for the source registry and source protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.contracts import ModelSource
from genesis_worker.registries import SourceRegistry
from genesis_worker.settings import PathsSettings, Settings


def test_huggingface_and_lmstudio_are_discovered() -> None:
    """Auto-discovery picks up both built-in source subpackages.

    Core extensibility property: drop a new package under
    ``genesis_worker.sources/`` and the registry finds it. The same
    mechanism that handles the built-in packages handles new ones.
    """
    names = {s.name for s in SourceRegistry(Settings()).all()}
    assert names == {"huggingface", "lmstudio"}


def test_source_classes_satisfy_protocol() -> None:
    """Every discovered source is an instance of ModelSource (runtime-checkable)."""
    for s in SourceRegistry(Settings()).all():
        assert isinstance(s, ModelSource)


# ---------------------------------------------------------------------------
# SourceRegistry facade — path-resolution contract
# ---------------------------------------------------------------------------


def test_registry_default_uses_vault_subdir() -> None:
    """No override -> settings.paths.resolved_vault_path / source.vault_subdir."""
    reg = SourceRegistry(Settings(paths=PathsSettings(vault_path=Path("/v"))))
    assert reg.get("huggingface").local_path == Path("/v/huggingface/hub")
    assert reg.get("lmstudio").local_path == Path("/v/lmstudio/models")


def test_registry_explicit_absolute_path_wins() -> None:
    """An absolute local_path bypasses the vault entirely."""
    s = Settings(
        paths=PathsSettings(vault_path=Path("/v")),
        sources={"huggingface": {"local_path": Path("/srv/external/hf")}},
    )
    reg = SourceRegistry(s)
    assert reg.get("huggingface").local_path == Path("/srv/external/hf")
    # lmstudio unaffected, still defaults
    assert reg.get("lmstudio").local_path == Path("/v/lmstudio/models")


def test_registry_explicit_relative_path_joins_vault() -> None:
    """A relative local_path is joined with resolved_vault_path."""
    s = Settings(
        paths=PathsSettings(vault_path=Path("/v")),
        sources={"huggingface": {"local_path": Path("custom/hf-subdir")}},
    )
    reg = SourceRegistry(s)
    assert reg.get("huggingface").local_path == Path("/v/custom/hf-subdir")


def test_registry_explicit_relative_path_for_lmstudio() -> None:
    """Same resolution rule applies to LM Studio's local_path override."""
    s = Settings(
        paths=PathsSettings(vault_path=Path("/v")),
        sources={"lmstudio": {"local_path": Path("alt/lms")}},
    )
    reg = SourceRegistry(s)
    assert reg.get("lmstudio").local_path == Path("/v/alt/lms")


def test_registry_unknown_source_raises() -> None:
    """``get()`` raises KeyError for an unknown source name."""
    with pytest.raises(KeyError):
        SourceRegistry(Settings()).get("does_not_exist")


def test_registry_all_returns_every_source() -> None:
    """``.all()`` returns one entry per discovered source."""
    sources = SourceRegistry(Settings()).all()
    assert {src.name for src in sources} == {"huggingface", "lmstudio"}


def test_registry_local_path_is_public_attribute() -> None:
    """``local_path`` is a public attribute — no private storage, no property.

    The framework sets it at construction; sources read it directly.
    No ``_local_path`` indirection, no ``@property`` boilerplate.
    """
    for src in SourceRegistry(Settings(paths=PathsSettings(vault_path=Path("/v")))).all():
        # Public attribute: readable directly, no descriptor protocol.
        assert isinstance(src.local_path, Path)
        # No private backing attribute.
        assert not hasattr(src, "_local_path")


def test_registry_vault_path_property() -> None:
    """The registry exposes its resolved vault path for downstream consumers."""
    reg = SourceRegistry(Settings(paths=PathsSettings(vault_path=Path("/v"))))
    assert reg.vault_path == Path("/v")


def test_registry_constructs_each_source_exactly_once() -> None:
    """A fresh SourceRegistry constructs sources; the previous one is garbage-collected.

    Each ``SourceRegistry`` instance owns its own source instances,
    so swapping settings (e.g. for tests) doesn't leak state between runs.
    """
    s1 = Settings(paths=PathsSettings(vault_path=Path("/v1")))
    s2 = Settings(paths=PathsSettings(vault_path=Path("/v2")))
    r1 = SourceRegistry(s1)
    r2 = SourceRegistry(s2)
    # Same class, different instances, different paths.
    assert r1.get("huggingface") is not r2.get("huggingface")
    assert r1.get("huggingface").local_path == Path("/v1/huggingface/hub")
    assert r2.get("huggingface").local_path == Path("/v2/huggingface/hub")
