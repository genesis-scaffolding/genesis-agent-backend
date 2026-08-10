"""Tests for the service registry facade."""

from __future__ import annotations

import pytest

from genesis_worker.services import ServiceRegistry
from genesis_worker.settings import (
    LlamaSwapServiceSettings,
    ServicesSettings,
    Settings,
)

# ---------------------------------------------------------------------------
# Construction contract
# ---------------------------------------------------------------------------


def test_registry_requires_explicit_classes() -> None:
    """No auto-discovery. Empty class list yields no services."""
    s = Settings()
    reg = ServiceRegistry(s, [])
    assert reg.all() == []
    with pytest.raises(KeyError):
        reg.get("llama_swap")


def test_registry_constructs_with_explicit_classes() -> None:
    """A service class passed in is instantiated exactly once."""

    class FakeService:
        name = "fake"

        def __init__(self, settings) -> None:
            self.settings = settings

    reg = ServiceRegistry(Settings(), [FakeService])
    svc = reg.get("fake")
    assert isinstance(svc, FakeService)


def test_registry_passes_per_service_settings() -> None:
    """Per-service settings slice is forwarded as the ``settings`` kwarg."""

    class LlamaSwapMock:
        name = "llama_swap"

        def __init__(self, settings) -> None:
            self.settings = settings

    s = Settings(
        services=ServicesSettings(
            llama_swap=LlamaSwapServiceSettings(listen_addr="0.0.0.0:9000"),
        ),
    )
    svc = ServiceRegistry(s, [LlamaSwapMock]).get("llama_swap")
    assert svc.settings.listen_addr == "0.0.0.0:9000"


def test_registry_passes_none_for_services_without_settings_slice() -> None:
    """Services whose name isn't on ``settings.services`` get ``settings=None``."""

    class UnlistedService:
        name = "unlisted"

        def __init__(self, settings) -> None:
            self.settings = settings

    svc = ServiceRegistry(Settings(), [UnlistedService]).get("unlisted")
    assert svc.settings is None


def test_registry_unknown_service_raises() -> None:
    """``get()`` raises KeyError for an unknown service name."""

    class A:
        name = "a"

        def __init__(self, settings) -> None:
            pass

    with pytest.raises(KeyError):
        ServiceRegistry(Settings(), [A]).get("does_not_exist")


def test_registry_all_returns_every_service() -> None:
    """``.all()`` returns one entry per registered service, in registration order."""

    class A:
        name = "a"

        def __init__(self, settings) -> None:
            pass

    class B:
        name = "b"

        def __init__(self, settings) -> None:
            pass

    reg = ServiceRegistry(Settings(), [A, B])
    assert [svc.name for svc in reg.all()] == ["a", "b"]


def test_registry_constructs_each_service_exactly_once() -> None:
    """A fresh ServiceRegistry constructs services; the previous is garbage-collected."""
    s1 = Settings(services=ServicesSettings(llama_swap=LlamaSwapServiceSettings(listen_addr="a:1")))
    s2 = Settings(services=ServicesSettings(llama_swap=LlamaSwapServiceSettings(listen_addr="a:2")))

    class LlamaSwapMock:
        name = "llama_swap"

        def __init__(self, settings) -> None:
            self.settings = settings

    r1 = ServiceRegistry(s1, [LlamaSwapMock])
    r2 = ServiceRegistry(s2, [LlamaSwapMock])
    assert r1.get("llama_swap") is not r2.get("llama_swap")
    assert r1.get("llama_swap").settings.listen_addr == "a:1"
    assert r2.get("llama_swap").settings.listen_addr == "a:2"
