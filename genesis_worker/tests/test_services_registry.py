"""Tests for the service registry facade and the InferenceService Protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.services import (
    InferenceService,
    ServiceCapabilities,
    ServiceRegistry,
)
from genesis_worker.services.llama_swap import LlamaSwapService
from genesis_worker.settings import (
    LlamaSwapServiceSettings,
    ServicesSettings,
    Settings,
)

# ---------------------------------------------------------------------------
# Auto-discovery contract
# ---------------------------------------------------------------------------


def test_llama_swap_is_auto_discovered() -> None:
    """The registry finds the llama_swap subpackage without an explicit class list.

    Core extensibility property: drop a new service subpackage under
    ``genesis_worker.services/`` and the registry finds it.
    """
    names = {svc.name for svc in ServiceRegistry(Settings()).all()}
    assert "llama_swap" in names


def test_llama_swap_service_is_constructible_via_registry() -> None:
    """``LlamaSwapService`` plugs into ServiceRegistry just like sources plug into SourceRegistry."""
    reg = ServiceRegistry(Settings())
    svc = reg.get("llama_swap")
    assert isinstance(svc, LlamaSwapService)


def test_llama_swap_service_satisfies_inference_service_protocol() -> None:
    """LlamaSwapService is a runtime-checkable InferenceService."""
    svc = ServiceRegistry(Settings()).get("llama_swap")
    assert isinstance(svc, InferenceService)


def test_registry_unknown_service_raises() -> None:
    """``get()`` raises KeyError for an unknown service name."""
    with pytest.raises(KeyError):
        ServiceRegistry(Settings()).get("does_not_exist")


def test_registry_all_returns_every_service() -> None:
    """``.all()`` returns one entry per discovered service."""
    reg = ServiceRegistry(Settings())
    assert {svc.name for svc in reg.all()} == {"llama_swap"}


# ---------------------------------------------------------------------------
# Per-service settings passthrough
# ---------------------------------------------------------------------------


def test_registry_passes_per_service_settings() -> None:
    """Per-service settings slice is forwarded as the ``settings`` kwarg."""
    s = Settings(
        services=ServicesSettings(
            llama_swap=LlamaSwapServiceSettings(listen_addr="0.0.0.0:9000"),
        ),
    )
    svc = ServiceRegistry(s).get("llama_swap")
    assert svc._settings.listen_addr == "0.0.0.0:9000"


def test_registry_constructs_each_service_exactly_once() -> None:
    """A fresh ServiceRegistry constructs services; the previous is garbage-collected."""
    s1 = Settings(services=ServicesSettings(llama_swap=LlamaSwapServiceSettings(listen_addr="a:1")))
    s2 = Settings(services=ServicesSettings(llama_swap=LlamaSwapServiceSettings(listen_addr="a:2")))
    r1 = ServiceRegistry(s1)
    r2 = ServiceRegistry(s2)
    assert r1.get("llama_swap") is not r2.get("llama_swap")
    assert r1.get("llama_swap")._settings.listen_addr == "a:1"
    assert r2.get("llama_swap")._settings.listen_addr == "a:2"


# ---------------------------------------------------------------------------
# LlamaSwapService — concrete implementation
# ---------------------------------------------------------------------------


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
    svc = ServiceRegistry(s).get("llama_swap")
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


def test_service_capabilities_is_a_dataclass() -> None:
    """ServiceCapabilities is frozen — the capability set is immutable per service."""
    caps = ServiceCapabilities(
        can_generate_config=True,
        can_export_for_agent=False,
        can_serve_llm=True,
        can_serve_image=False,
        can_train_models=False,
        has_web_ui=False,
    )
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        caps.can_serve_llm = False  # type: ignore[misc]
