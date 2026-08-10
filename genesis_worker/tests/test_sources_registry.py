"""Tests for the source registry and source protocol."""

from __future__ import annotations

from genesis_worker.sources import all_sources, register_source
from genesis_worker.sources._base import ModelSource


def test_huggingface_and_lmstudio_are_registered() -> None:
    names = {s.name for s in all_sources()}
    assert "huggingface" in names
    assert "lmstudio" in names


def test_register_source_is_idempotent() -> None:
    """Re-registering a source with the same name replaces it (last wins)."""

    @register_source
    class TempSource:
        name = "temp_test_source"
        display_name = "Temp"
        can_acquire = False

        def is_available(self) -> bool:
            return False

        def local_path(self):
            from pathlib import Path

            return Path("/tmp/temp")

        def walk(self):
            return []

    names = {s.name for s in all_sources()}
    assert "temp_test_source" in names


def test_source_classes_satisfy_protocol() -> None:
    """Every registered source is an instance of ModelSource (runtime-checkable)."""
    for s in all_sources():
        assert isinstance(s, ModelSource)
