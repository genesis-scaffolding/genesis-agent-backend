"""Parallel to ``test_plugin_boundary.py`` for YAML-declared services.

The plugin boundary test walks ``*.py`` files only — YAML files are
invisible to it. This test is the corresponding gate for YAML-declared
services: each ``services/_declarative/*.yaml`` must parse, construct
against a fake ``ServiceContext``, and expose the identity /
capability / auth surface that the framework relies on.

Phase 2 ships an empty ``services/_declarative/`` (marker package).
Phase 3 adds the built-in YAMLs (``crawl4ai``, ``sillytavern``); this
test expands to assert they construct correctly and are discoverable
through :class:`ServiceRegistry`.

No docker is involved — the loader constructs the service object
without starting the container.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from genesis_worker.contracts import (
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
)
from genesis_worker.registries import ServiceRegistry
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import (
    DockerService,
    load_service_spec,
)

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


def test_declarative_dir_exists() -> None:
    """Phase 2 puts an ``__init__.py`` marker here so ``importlib.resources`` resolves it."""
    assert _DECLARATIVE_DIR.is_dir()
    assert (_DECLARATIVE_DIR / "__init__.py").is_file()


def test_declarative_marker_init_is_empty() -> None:
    """The marker must not import anything -- it's a Python module, not a plugin.

    Mirrors the contract ``test_plugin_boundary.py`` enforces for every
    ``__init__.py`` under ``services/``.
    """
    import ast

    init_text = (_DECLARATIVE_DIR / "__init__.py").read_text()
    tree = ast.parse(init_text)
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert imports == [], (
        f"services/_declarative/__init__.py must have no imports; found: {ast.dump(tree)}"
    )


def test_declarative_dir_has_no_python_modules_other_than_init() -> None:
    """Anything else under ``services/_declarative/`` would be a module the plugin
    boundary test walks. We want zero non-init modules so the boundary test passes
    without surprises; YAMLs are invisible to it.
    """
    found = [p for p in _DECLARATIVE_DIR.glob("*.py") if p.name != "__init__.py"]
    assert found == [], f"unexpected python modules: {found}"


# --- built-in YAMLs construct ----------------------------------------------


_BUILT_IN_SPECS = ("bifrost.yaml", "crawl4ai.yaml", "sillytavern.yaml")


def _load_spec(name: str, tmp_path: Path) -> DockerService:
    """Parse + construct one built-in YAML spec against a hermetic context."""
    path = _DECLARATIVE_DIR / name
    assert path.is_file(), f"missing built-in spec: {path}"
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name=path.stem))
    assert isinstance(svc, DockerService)
    return svc


@pytest.mark.parametrize("spec_name", _BUILT_IN_SPECS)
def test_built_in_spec_parses_and_constructs(spec_name: str, tmp_path: Path) -> None:
    """Every built-in YAML must parse + construct via the framework loader."""
    svc = _load_spec(spec_name, tmp_path)
    assert isinstance(svc, DockerService), f"{spec_name}: expected DockerService"


@pytest.mark.parametrize("spec_name", _BUILT_IN_SPECS)
def test_built_in_spec_identity(spec_name: str, tmp_path: Path) -> None:
    """``name`` / ``display_name`` / ``category`` / ``description`` come from the YAML."""
    svc = _load_spec(spec_name, tmp_path)
    assert svc.name == Path(spec_name).stem
    assert svc.display_name, f"{spec_name}: display_name is empty"
    assert isinstance(svc.category, ServiceCategory), f"{spec_name}: bad category"
    assert isinstance(svc.description, str) and svc.description, (
        f"{spec_name}: description is empty"
    )


@pytest.mark.parametrize("spec_name", _BUILT_IN_SPECS)
def test_built_in_spec_capabilities_and_resource(spec_name: str, tmp_path: Path) -> None:
    """Capabilities + resource estimate come from the YAML, not hard-coded defaults."""
    svc = _load_spec(spec_name, tmp_path)
    caps = svc.capabilities()
    assert isinstance(caps, ServiceCapabilities)
    # Every built-in ships as docker-with-web-UI + installable.
    assert caps.has_web_ui is True
    assert caps.can_install is True
    # crawl4ai + sillytavern don't serve LLMs; bifrost does (LLM gateway).
    if spec_name == "bifrost.yaml":
        assert caps.can_serve_llm is True
    else:
        assert caps.can_serve_llm is False
    resource = svc.resource_estimate()
    assert isinstance(resource, ServiceResourceEstimate)
    # Both built-ins have no GPU requirement.
    assert resource.vram_bytes_typical == 0
    assert resource.vram_bytes_min == 0


@pytest.mark.parametrize("spec_name", _BUILT_IN_SPECS)
@pytest.mark.integration
def test_built_in_spec_unavailable_before_install(spec_name: str, tmp_path: Path) -> None:
    """A fresh context has no pulled image — ``is_available()`` must report False.

    Integration: probes the host's docker daemon via ``DockerContainer.image_present``.
    """
    svc = _load_spec(spec_name, tmp_path)
    assert svc.is_available() is False


# --- bifrost-specific -----------------------------------------------------


def test_bifrost_identity(tmp_path: Path) -> None:
    """Bifrost's identity / category / description match the spec's surface."""
    svc = _load_spec("bifrost.yaml", tmp_path)
    assert svc.name == "bifrost"
    assert svc.display_name == "Bifrost"
    assert svc.category.value == "llm"
    assert svc.config.listen_port == 8090  # off llama-swap (8080) and worker API (9090)
    assert svc.image_ref == "maximhq/bifrost:latest"


# --- crawl4ai-specific ----------------------------------------------------


def test_crawl4ai_identity(tmp_path: Path) -> None:
    """Crawl4AI's identity / category / description match the ADR's surface."""
    svc = _load_spec("crawl4ai.yaml", tmp_path)
    assert svc.name == "crawl4ai"
    assert svc.display_name == "Crawl4AI"
    assert svc.category == ServiceCategory.CRAWLER
    assert svc.description == "Web crawler + dashboard"


def test_crawl4ai_image_and_container(tmp_path: Path) -> None:
    """Image ref + container name + listen port match the upstream defaults."""
    svc = _load_spec("crawl4ai.yaml", tmp_path)
    assert svc.image_ref == "unclecode/crawl4ai:latest"
    assert svc.container_name == "crawl4ai"
    assert svc.listen_address == "0.0.0.0:11235"


def test_crawl4ai_auth_token_callable_when_no_file(tmp_path: Path) -> None:
    """``auth_token()`` is callable and returns ``None`` until the pre-start hook runs.

    The ``ensure_persistent_token`` hook fires inside ``start()`` — the
    YAML's pre-start hooks fire there, not at construction time. So at
    construction, no token file exists yet, ``jwt_enabled`` is False
    (default), and the token machinery reports None. ``start()`` would
    generate and persist one, but we don't run docker here.
    """
    svc = _load_spec("crawl4ai.yaml", tmp_path)
    assert callable(svc.auth_token)
    assert callable(svc.auth_enabled)
    assert svc.auth_token() is None
    assert svc.auth_enabled() is False


def test_crawl4ai_auth_disabled_when_jwt_enabled(tmp_path: Path) -> None:
    """``jwt_enabled: true`` short-circuits the token machinery."""
    ctx = service_ctx(tmp_path, name="crawl4ai", options={"jwt_enabled": True})
    svc = _load_spec("crawl4ai.yaml", tmp_path)
    # ``ctx.options`` is the input; ``_load_spec`` rebuilds the context from
    # the hermetic tmp_path, so re-bind via a fresh load with this ctx.
    svc = load_service_spec(_DECLARATIVE_DIR / "crawl4ai.yaml", ctx=ctx)
    assert isinstance(svc, DockerService)
    assert svc.auth_token() is None
    assert svc.auth_enabled() is True


def test_crawl4ai_ui_panels_include_auth_token(tmp_path: Path) -> None:
    """The crawl4ai spec opts in to the ``auth_token`` panel; the framework honours it."""
    svc = _load_spec("crawl4ai.yaml", tmp_path)
    assert "auth_token" in svc.ui_panels
    assert "container_info" in svc.ui_panels
    assert "log_tail" in svc.ui_panels


def test_crawl4ai_generated_token_is_64_hex(tmp_path: Path) -> None:
    """The token generator the YAML references produces a 64-char hex string.

    The YAML's pre-start hook uses ``random_hex_32`` (registered in
    ``utils/services/hooks.py``). We invoke the same generator here
    because the YAML file's contract is that token format.
    """
    from genesis_worker.utils.services.ensure_persistent_file import random_hex_32

    token = random_hex_32()
    assert len(token) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", token), f"token is not 64 hex chars: {token!r}"


# --- sillytavern-specific -------------------------------------------------


def test_sillytavern_identity(tmp_path: Path) -> None:
    """SillyTavern's identity / category / description match the ADR's surface."""
    svc = _load_spec("sillytavern.yaml", tmp_path)
    assert svc.name == "sillytavern"
    assert svc.display_name == "SillyTavern"
    assert svc.category == ServiceCategory.CHAT
    assert svc.description == "LLM chat front-end"


def test_sillytavern_image_and_container(tmp_path: Path) -> None:
    """Image ref + container name + listen port match the upstream defaults."""
    svc = _load_spec("sillytavern.yaml", tmp_path)
    assert svc.image_ref == "ghcr.io/sillytavern/sillytavern:release"
    assert svc.container_name == "sillytavern"
    assert svc.listen_address == "0.0.0.0:8000"


def test_sillytavern_has_no_auth_block(tmp_path: Path) -> None:
    """SillyTavern has no ``auth:`` block — the auth-token panel must not render.

    The framework's auth_token panel reads ``svc.auth_token()`` /
    ``svc.auth_enabled()``; with no AuthConfig, the DockerService base
    reports ``None`` / ``False`` so the panel renders "no token yet"
    rather than the JWT/token UX. The service nonetheless inherits
    ``auth_token`` / ``auth_enabled`` methods from ``DockerService``
    (defined there for every service), but they are inert.
    """
    svc = _load_spec("sillytavern.yaml", tmp_path)
    assert svc.config.auth is None
    assert svc.auth_token() is None
    assert svc.auth_enabled() is False
    # The YAML's ``ui.status_panels`` does NOT include ``auth_token`` —
    # the framework just won't render it.
    assert "auth_token" not in svc.ui_panels


def test_sillytavern_pre_start_hooks_seeded(tmp_path: Path) -> None:
    """SillyTavern seeds the whitelist hook on its config (no start call needed).

    The loader resolves ``$data_dir/config/config.yaml`` to an
    absolute path against ``ctx.data_dir`` at construction time, so
    the hook entry the runtime sees is already concrete.
    """
    svc = _load_spec("sillytavern.yaml", tmp_path)
    hooks = list(svc.config.pre_start_hooks)
    assert len(hooks) == 1
    assert hooks[0]["kind"] == "seed_yaml_whitelist"
    assert hooks[0]["key"] == "whitelist"
    assert "100.64.0.0/10" in hooks[0]["extras"]
    assert hooks[0]["disable_docker_hosts"] is True
    expected_target = str(tmp_path / "data" / "config" / "config.yaml")
    assert hooks[0]["target"] == expected_target


def test_sillytavern_ui_panels_additive_to_default(tmp_path: Path) -> None:
    """SillyTavern's YAML declares ``container_info`` and ``log_tail``.

    The default docker set is ``(service_info, container_info,
    log_tail)``; the YAML entries are already in the default so the
    additive merge is a no-op. ``service_info`` is always present so
    the install / start / stop controls render.
    """
    svc = _load_spec("sillytavern.yaml", tmp_path)
    assert svc.ui_panels == ("service_info", "container_info", "log_tail")


# --- registry discovers YAMLs ---------------------------------------------


def test_service_registry_discovers_declarative_specs(tmp_path: Path) -> None:
    """``ServiceRegistry`` walks Python + YAML; both built-ins are reachable.

    Uses a minimal ``Settings`` with isolated XDG paths so the
    registry bootstrap doesn't touch the host's real install.
    """
    from genesis_worker.settings import PathsSettings, Settings

    settings = Settings(paths=PathsSettings(state_dir=tmp_path / "state"))
    registry = ServiceRegistry(settings)

    crawl4ai = registry.get("crawl4ai")
    sillytavern = registry.get("sillytavern")
    assert crawl4ai.name == "crawl4ai"
    assert sillytavern.name == "sillytavern"
    # Both should be the framework's DockerService, not the old Python plugins.
    assert isinstance(crawl4ai, DockerService)
    assert isinstance(sillytavern, DockerService)


def test_no_python_crawl4ai_or_sillytavern_modules() -> None:
    """Phase 3 deletes the old Python service packages — only YAML remains."""
    services_root = Path(__file__).resolve().parent.parent / "services"
    for stale in ("crawl4ai", "sillytavern"):
        assert not (services_root / stale).is_dir(), (
            f"{stale}/ should be deleted in phase 3; "
            f"found {sorted(p.name for p in (services_root / stale).iterdir())}"
        )
