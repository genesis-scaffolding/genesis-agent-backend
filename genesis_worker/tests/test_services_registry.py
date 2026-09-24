"""Tests for the service registry and the InferenceService contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.contracts import InferenceService, ServiceCapabilities
from genesis_worker.registries import ServiceRegistry
from genesis_worker.services.llama_swap import LlamaSwapService
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.tests._factories import service_ctx


def _llama_swap(registry: ServiceRegistry) -> LlamaSwapService:
    svc = registry.get("llama_swap")
    assert isinstance(svc, LlamaSwapService)
    return svc


# ---------------------------------------------------------------------------
# Auto-discovery contract
# ---------------------------------------------------------------------------


def test_llama_swap_is_auto_discovered() -> None:
    """Drop a new service subpackage under services/ and the registry finds it."""
    names = {svc.name for svc in ServiceRegistry(Settings()).all()}
    assert "llama_swap" in names


def test_llama_swap_service_is_constructible_via_registry() -> None:
    reg = ServiceRegistry(Settings())
    assert isinstance(reg.get("llama_swap"), LlamaSwapService)


def test_llama_swap_service_is_an_inference_service() -> None:
    assert isinstance(ServiceRegistry(Settings()).get("llama_swap"), InferenceService)


def test_registry_unknown_service_raises() -> None:
    with pytest.raises(KeyError):
        ServiceRegistry(Settings()).get("does_not_exist")


def test_registry_all_returns_every_service() -> None:
    assert {svc.name for svc in ServiceRegistry(Settings()).all()} == {
        "llama_swap",
        "cptr",
        "comfyui",
        "sillytavern",
        "crawl4ai",
        "bifrost",
        "freshrss",
        "ntfy",
        "photoprism",
    }


def test_comfyui_service_is_auto_discovered() -> None:
    """Adding the comfyui subpackage should auto-register it."""
    names = {svc.name for svc in ServiceRegistry(Settings()).all()}
    assert "comfyui" in names


def test_sillytavern_service_is_auto_discovered() -> None:
    """Adding the sillytavern subpackage should auto-register it."""

    names = {svc.name for svc in ServiceRegistry(Settings()).all()}
    assert "sillytavern" in names


def test_crawl4ai_service_is_auto_discovered() -> None:
    """Adding the crawl4ai subpackage should auto-register it."""

    names = {svc.name for svc in ServiceRegistry(Settings()).all()}
    assert "crawl4ai" in names


def test_ntfy_service_is_auto_discovered() -> None:
    """ntfy is a YAML-declared service; the registry discovers it from
    ``services/_declarative/ntfy.yaml``.
    """

    names = {svc.name for svc in ServiceRegistry(Settings()).all()}
    assert "ntfy" in names


def test_freshrss_service_is_auto_discovered() -> None:
    """freshrss is a YAML-declared service; the registry discovers it from
    ``services/_declarative/freshrss.yaml``.
    """

    names = {svc.name for svc in ServiceRegistry(Settings()).all()}
    assert "freshrss" in names


def test_abstract_service_cannot_be_instantiated(tmp_path: Path) -> None:
    """The ABC enforces the contract; a partial implementation fails at construction."""

    class Partial(InferenceService):
        name = "partial"
        display_name = "Partial"

    with pytest.raises(TypeError):
        Partial(service_ctx(tmp_path))  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Options passthrough — the framework carries the slice, the plugin parses it
# ---------------------------------------------------------------------------


def test_registry_passes_option_slice_to_plugin() -> None:
    s = Settings(services={"llama_swap": {"listen_addr": "0.0.0.0:9000"}})
    svc = _llama_swap(ServiceRegistry(s))
    assert svc._options.listen_addr == "0.0.0.0:9000"


def test_registry_constructs_each_service_exactly_once() -> None:
    s1 = Settings(services={"llama_swap": {"listen_addr": "a:1"}})
    s2 = Settings(services={"llama_swap": {"listen_addr": "a:2"}})
    r1, r2 = ServiceRegistry(s1), ServiceRegistry(s2)
    assert r1.get("llama_swap") is not r2.get("llama_swap")
    assert _llama_swap(r1)._options.listen_addr == "a:1"
    assert _llama_swap(r2)._options.listen_addr == "a:2"


def test_service_defaults_when_no_option_slice(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    # Default is 0.0.0.0:8080 so the service is reachable from the
    # LAN/VPN, not just localhost. Users who want a different bind
    # address override via GENESIS_SERVICES__LLAMA_SWAP__LISTEN_ADDR.
    assert svc._options.listen_addr == "0.0.0.0:8080"
    assert svc._options.session_name == "swap"
    assert svc._options.public_host is None  # falls back to socket.gethostname()


def test_registry_scopes_paths_by_dir_name() -> None:
    """``llama_swap`` writes under ``<data_dir>/llama-swap/`` (ADR-009)."""
    s = Settings(paths=PathsSettings(data_dir=Path("/d")))
    svc = ServiceRegistry(s).get("llama_swap")
    assert svc.config_path == Path("/d/llama-swap/config.yaml")


# ---------------------------------------------------------------------------
# LlamaSwapService surface
# ---------------------------------------------------------------------------


def test_llama_swap_capabilities_declares_llm_serving(tmp_path: Path) -> None:
    caps = LlamaSwapService(service_ctx(tmp_path)).capabilities()
    assert caps.can_serve_llm
    assert caps.can_generate_config
    assert caps.can_export_for_agent
    assert not caps.can_serve_image
    assert not caps.can_train_models
    assert caps.has_web_ui  # llama-swap has its own web UI on :8080


def test_is_available_tracks_the_binary_only(tmp_path: Path) -> None:
    """Availability means the binary is installed. A missing config is not
    unavailability — the UI offers to generate one (ADR-009)."""
    from genesis_worker.services.llama_swap.installs import LlamaSwapBinary

    svc = LlamaSwapService(service_ctx(tmp_path))
    assert not svc.config_path.exists()
    assert svc.is_available() is False

    # Lay down a v0.4.5 install with a real binary at the expected path.
    installable = svc.installs()[0]
    assert isinstance(installable, LlamaSwapBinary)
    version = "v0.4.5"
    install_root = installable._layout.installs_root / version
    install_root.mkdir(parents=True)
    binary = install_root / "llama-swap"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    installable._layout.set_current_symlink(version)

    assert svc.is_available() is True


def test_lifecycle_methods_exist(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    for method in (svc.start, svc.stop, svc.status, svc.is_running, svc.runtime_endpoint):
        assert callable(method)
    est = svc.resource_estimate()
    assert est.vram_bytes_typical > 0


def test_service_capabilities_is_frozen() -> None:
    caps = ServiceCapabilities(
        can_generate_config=True,
        can_export_for_agent=False,
        can_serve_llm=True,
        can_serve_image=False,
        can_train_models=False,
        has_web_ui=False,
    )
    with pytest.raises(Exception):
        caps.can_serve_llm = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# rebuild (ADR-036)
# ---------------------------------------------------------------------------


def test_rebuild_replaces_cached_instance(tmp_path: Path) -> None:
    """A rebuild swaps the cached instance for one built against fresh options."""
    settings = Settings(paths=PathsSettings(config_dir=tmp_path / "config"))
    reg = ServiceRegistry(settings)
    svc_before = reg.get("crawl4ai")
    # Rebuild -- ctx is rebuilt from settings, but with no override changes the
    # result should be a new instance with the same effective config.
    svc_after = reg.rebuild("crawl4ai")
    assert svc_after is not svc_before
    assert svc_after.name == "crawl4ai"
    assert isinstance(svc_after, InferenceService)


def test_rebuild_refuses_when_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from genesis_worker.utils.process.docker import DockerContainer

    settings = Settings(paths=PathsSettings(config_dir=tmp_path / "config"))
    reg = ServiceRegistry(settings)
    monkeypatch.setattr(DockerContainer, "is_running", lambda self: True)
    with pytest.raises(RuntimeError, match="running"):
        reg.rebuild("crawl4ai")


def test_rebuild_unknown_service_raises(tmp_path: Path) -> None:
    settings = Settings(paths=PathsSettings(config_dir=tmp_path / "config"))
    reg = ServiceRegistry(settings)
    with pytest.raises(KeyError, match="no declarative YAML"):
        reg.rebuild("does_not_exist")


def test_rebuild_python_service_raises(tmp_path: Path) -> None:
    """Python services (no YAML spec path) cannot be rebuilt via this path.

    The configure panel doesn't render for them; this branch is the
    defence against an accidental call.
    """
    settings = Settings(paths=PathsSettings(config_dir=tmp_path / "config"))
    reg = ServiceRegistry(settings)
    # ``llama_swap`` is a Python plugin (no YAML).
    with pytest.raises(KeyError, match="no declarative YAML"):
        reg.rebuild("llama_swap")
