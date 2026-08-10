"""Tests for the service registry facade and the InferenceService Protocol."""

from __future__ import annotations

import shutil

import pytest

from genesis_worker.services import (
    InferenceService,
    ServiceRegistry,
)
from genesis_worker.services.llama_swap import LlamaSwapService
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


# ---------------------------------------------------------------------------
# LlamaSwapService — concrete implementation
# ---------------------------------------------------------------------------


def test_llama_swap_service_is_constructible_via_registry() -> None:
    """``LlamaSwapService`` plugs into ServiceRegistry just like sources plug into SourceRegistry."""
    reg = ServiceRegistry(Settings(), [LlamaSwapService])
    svc = reg.get("llama_swap")
    assert isinstance(svc, LlamaSwapService)


def test_llama_swap_service_satisfies_inference_service_protocol() -> None:
    """LlamaSwapService is a runtime-checkable InferenceService."""
    svc = ServiceRegistry(Settings(), [LlamaSwapService]).get("llama_swap")
    assert isinstance(svc, InferenceService)


def test_llama_swap_service_default_settings() -> None:
    """Construction with no settings yields sensible defaults."""
    svc = LlamaSwapService()
    assert svc._settings.listen_addr == "127.0.0.1:8080"
    assert svc._settings.session_name == "swap"


def test_llama_swap_service_picks_up_settings_slice() -> None:
    """When constructed via ServiceRegistry, the per-service slice is used."""
    s = Settings(
        services=ServicesSettings(
            llama_swap=LlamaSwapServiceSettings(
                listen_addr="0.0.0.0:9999",
                session_name="custom-session",
            ),
        ),
    )
    svc = ServiceRegistry(s, [LlamaSwapService]).get("llama_swap")
    assert svc._settings.listen_addr == "0.0.0.0:9999"
    assert svc._settings.session_name == "custom-session"


def test_llama_swap_capabilities_declares_llm_serving() -> None:
    """LlamaSwapService reports the capabilities the dashboard relies on."""
    svc = LlamaSwapService()
    caps = svc.capabilities()
    assert caps.can_serve_llm
    assert caps.can_generate_config
    assert caps.can_export_for_agent
    assert not caps.can_serve_image
    assert not caps.can_train_models
    assert not caps.has_web_ui


def test_llama_swap_is_available_when_binary_on_path() -> None:
    """``is_available()`` checks for the ``llama-swap`` binary on PATH."""
    svc = LlamaSwapService()
    # The result depends on whether llama-swap is installed on the test
    # machine. We assert the method runs without error and returns a bool.
    result = svc.is_available()
    assert isinstance(result, bool)
    if shutil.which("llama-swap") is None:
        assert result is False


def test_llama_swap_is_available_false_when_config_missing(tmp_path: Path) -> None:
    """``is_available()`` returns False when an explicit config_path doesn't exist."""
    svc = LlamaSwapService(
        settings=LlamaSwapServiceSettings(config_path=tmp_path / "missing.yaml"),
    )
    assert svc.is_available() is False


def test_llama_swap_lifecycle_methods_are_stubbed() -> None:
    """Lifecycle methods raise NotImplementedError until plan-002 lands them.

    The structural shape (Protocol conformance) is in place; the runtime
    plumbing (tmux + curl + psutil) ships in plan-002.
    """
    svc = LlamaSwapService()
    with pytest.raises(NotImplementedError):
        svc.start()
    with pytest.raises(NotImplementedError):
        svc.stop()
    with pytest.raises(NotImplementedError):
        svc.status()
    with pytest.raises(NotImplementedError):
        svc.is_running()
    with pytest.raises(NotImplementedError):
        svc.runtime_endpoint()
    with pytest.raises(NotImplementedError):
        svc.wait_ready(1.0)
    with pytest.raises(NotImplementedError):
        svc.resource_estimate()


# ---------------------------------------------------------------------------
# Imports for type annotations used above
# ---------------------------------------------------------------------------


from pathlib import Path  # noqa: E402
