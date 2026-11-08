"""Traefik service tests — the cluster ingress proxy.

The declarative spec is the worker's half of ADR-016: it declares the
proxy container, its TLS / DNS-01 options, and the pre-start hook that
materialises the orchestrator's route table into Traefik's dynamic
configuration.

Covered here:

- the spec's identity, kind and options,
- the GoDaddy credential wiring (Lego reads it from the environment),
- the ``routes`` materialisation hook (and the fact that a missing
  key fails the start loudly),
- the start path applying the orchestrator-supplied ``options`` section
  through the worker's own options machinery.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from genesis_worker import GenesisWorker
from genesis_worker.contracts import ServiceCategory
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.ports.allocator import NodePortAllocator
from genesis_worker.utils.services import ComposeService, load_service_spec
from genesis_worker.utils.services.hooks import PreStartHookContext
from genesis_worker.utils.services.hooks import run as run_hooks

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


def _allocator(tmp_path: Path) -> NodePortAllocator:
    """A port allocator with every probe stubbed — no docker, no real binds."""
    return NodePortAllocator(
        tmp_path / "state",
        fixed_ports=lambda: set(),
        container_bindings=list,
        bind_probe=lambda port: True,
    )


def _load(tmp_path: Path, options: dict | None = None) -> ComposeService:
    svc = load_service_spec(
        _DECLARATIVE_DIR / "traefik.yaml",
        ctx=service_ctx(
            tmp_path,
            name="traefik",
            options=options or {},
            port_allocator=_allocator(tmp_path),
        ),
    )
    assert isinstance(svc, ComposeService)
    return svc


def _render(svc: ComposeService) -> str:
    """The rendered compose file, with the host ports allocated.

    The start path renders through the port binder (``$auto_port.*`` is
    materialised there); rendering without it leaves the marker in the
    file and the framework refuses to write it.
    """
    return svc._render_compose_file(ports=svc.port_binder().resolver()).read_text()


# --- identity --------------------------------------------------------------


def test_traefik_is_a_compose_service(tmp_path: Path) -> None:
    """Two published ports (ingress + ping) — the docker kind publishes one."""
    svc = _load(tmp_path)
    assert isinstance(svc, ComposeService)


def test_traefik_identity(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    assert svc.name == "traefik"
    assert svc.display_name == "Traefik"
    assert svc.category is ServiceCategory.UTILITY
    assert svc.description


def test_traefik_image_is_declared(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    repos = {img.name: img.repo for img in svc.config.images}
    assert repos == {"traefik": "traefik"}


# --- options ---------------------------------------------------------------


def test_default_options_are_the_fleet_case(tmp_path: Path) -> None:
    """GoDaddy is the fleet's registrar; the ping port is loopback-only."""
    svc = _load(tmp_path)
    assert svc.options.dns_provider == "godaddyv3"  # type: ignore[attr-defined]
    assert svc.options.ingress_port == 443  # type: ignore[attr-defined]
    assert svc.options.listen_host == "127.0.0.1"  # type: ignore[attr-defined]


def test_the_ping_port_is_allocated_not_fixed(tmp_path: Path) -> None:
    """A fixed 8080 collided with llama-swap; the framework allocates one.

    Docker programs a container's endpoint ports atomically — one failed
    bind rolls the whole endpoint back, so the proxy came up with no
    published port at all (not even 443) and no network.
    """
    svc = _load(tmp_path)
    assert svc.options.listen_port == "$auto_port.listen"  # type: ignore[attr-defined]
    assert svc.config.listen_port == "$auto_port.listen"
    assert svc.declared_port_markers() == ("listen",)
    assert svc.declared_host_port() is None
    assert svc.effective_host_port() is None  # not started yet


def test_the_ingress_port_stays_fixed(tmp_path: Path) -> None:
    """443 is the fleet's stable entry point — the DNS record points at it."""
    svc = _load(tmp_path)
    assert svc.options.ingress_port == 443  # type: ignore[attr-defined]
    assert "443:443" in _render(svc)


def test_the_probe_follows_the_allocated_port(tmp_path: Path) -> None:
    """The health probe watches the allocated host port, not a fixed one."""
    svc = _load(tmp_path)
    assert svc.effective_host_port() is None  # never started
    _render(svc)
    assert svc.effective_host_port() == 18000


def test_health_probe_uses_the_plain_http_ping_entrypoint(tmp_path: Path) -> None:
    """The framework's probe is HTTP-only — the TLS port would never answer."""
    svc = _load(tmp_path)
    assert svc.config.health_probe_path == "/ping"


# --- rendered compose file -------------------------------------------------


def test_rendered_compose_carries_the_godaddy_token(tmp_path: Path) -> None:
    svc = _load(tmp_path, options={"dns_api_token": "the-token"})
    assert 'GODADDYV3_PERSONAL_ACCESS_TOKEN: "the-token"' in _render(svc)


def test_rendered_compose_file_is_not_world_readable(tmp_path: Path) -> None:
    """The compose file carries the DNS credential."""
    svc = _load(tmp_path, options={"dns_api_token": "the-token"})
    path = svc._render_compose_file(ports=svc.port_binder().resolver())
    assert path.stat().st_mode & 0o077 == 0


def test_rendered_compose_publishes_the_ingress_and_ping_ports(tmp_path: Path) -> None:
    """The ingress port is fixed; the ping port is the allocated one."""
    svc = _load(tmp_path)
    rendered = _render(svc)
    assert '- "443:443"' in rendered
    # The ping port: the allocated host port, the container side stays 8080.
    assert '- "127.0.0.1:18000:8080"' in rendered
    # The old fixed host port is gone — it collided with llama-swap.
    assert '"127.0.0.1:8080:8080"' not in rendered
    assert "$auto_port." not in rendered


def test_rendered_compose_uses_the_dns_provider_and_acme_email(tmp_path: Path) -> None:
    svc = _load(tmp_path, options={"acme_email": "admin@example.com"})
    rendered = _render(svc)
    assert "--certificatesResolvers.letsencrypt.acme.dnschallenge.provider=godaddyv3" in rendered
    assert "--certificatesResolvers.letsencrypt.acme.email=admin@example.com" in rendered
    assert "--certificatesResolvers.letsencrypt.acme.dnschallenge=true" in rendered
    assert "--certificatesResolvers.letsencrypt.acme.tlschallenge=false" in rendered


def test_rendered_compose_mounts_the_config_and_data_dirs(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    rendered = _render(svc)
    assert f"{tmp_path}/config:/etc/traefik" in rendered
    assert f"{tmp_path}/data/data:/data" in rendered


# --- the routes hook -------------------------------------------------------


def test_routes_hook_writes_the_dynamic_config(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    svc._pending_orchestrator_config = {
        "routes": {"http": {"routers": {"r": {"rule": "Host(`a.example`)"}}}},
        "options": {"dns_provider": "godaddyv3"},
    }
    run_hooks(
        list(svc.config.pre_start_hooks),
        PreStartHookContext(service=svc, state_dir=svc._ctx.state_dir, data_dir=svc._ctx.data_dir),
    )
    written = yaml.safe_load((tmp_path / "config" / "dynamic" / "dynamic.yml").read_text())
    assert written["http"]["routers"]["r"]["rule"] == "Host(`a.example`)"


def test_routes_hook_fails_loudly_on_a_missing_key(tmp_path: Path) -> None:
    """A half-supplied body must never fall back to a stale route table."""
    svc = _load(tmp_path)
    svc._pending_orchestrator_config = {"options": {"dns_provider": "godaddyv3"}}
    with pytest.raises(ValueError, match="routes"):
        run_hooks(
            list(svc.config.pre_start_hooks),
            PreStartHookContext(
                service=svc, state_dir=svc._ctx.state_dir, data_dir=svc._ctx.data_dir
            ),
        )


def test_routes_hook_is_a_no_op_without_an_orchestrator_config(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    run_hooks(
        list(svc.config.pre_start_hooks),
        PreStartHookContext(service=svc, state_dir=svc._ctx.state_dir, data_dir=svc._ctx.data_dir),
    )
    assert not (tmp_path / "config" / "dynamic" / "dynamic.yml").exists()


# --- the start path applies the orchestrator's options ---------------------


def _worker(tmp_path: Path) -> GenesisWorker:
    settings = Settings(
        paths=PathsSettings(
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            cache_dir=tmp_path / "cache",
            state_dir=tmp_path / "state",
            log_dir=tmp_path / "log",
        )
    )
    return GenesisWorker(settings=settings)


def test_orchestrator_options_are_applied_to_the_service_options(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    svc = worker._apply_orchestrator_options(
        "traefik",
        {"routes": {}, "options": {"dns_provider": "cloudflare", "acme_email": "a@b.c"}},
    )
    assert svc.options.dns_provider == "cloudflare"  # type: ignore[attr-defined]
    assert svc.options.acme_email == "a@b.c"  # type: ignore[attr-defined]


def test_orchestrator_options_reach_the_rendered_compose_file(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    svc = cast(
        ComposeService,
        worker._apply_orchestrator_options(
            "traefik",
            {"routes": {}, "options": {"dns_api_token": "pushed-token", "acme_email": "a@b.c"}},
        ),
    )
    rendered = _render(svc)
    assert 'GODADDYV3_PERSONAL_ACCESS_TOKEN: "pushed-token"' in rendered
    assert "--certificatesResolvers.letsencrypt.acme.email=a@b.c" in rendered


def test_orchestrator_options_are_persisted_in_the_workers_option_store(tmp_path: Path) -> None:
    """The worker holds whatever its options say — no parallel storage."""
    worker = _worker(tmp_path)
    worker._apply_orchestrator_options("traefik", {"options": {"acme_email": "a@b.c"}})
    sidecar = tmp_path / "config" / "services" / "traefik.overrides.json"
    assert "a@b.c" in sidecar.read_text()


def test_a_config_body_without_options_changes_nothing(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    svc = worker._apply_orchestrator_options("traefik", {"routes": {}})
    assert svc.options.dns_provider == "godaddyv3"  # type: ignore[attr-defined]
    assert not (tmp_path / "config" / "services" / "traefik.overrides.json").exists()
