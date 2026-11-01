"""Traefik service tests — the cluster ingress proxy.

The declarative spec is the worker's half of ADR-015: it declares the
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
from genesis_worker.utils.services import ComposeService, load_service_spec
from genesis_worker.utils.services.hooks import PreStartHookContext
from genesis_worker.utils.services.hooks import run as run_hooks

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


def _load(tmp_path: Path, options: dict | None = None) -> ComposeService:
    svc = load_service_spec(
        _DECLARATIVE_DIR / "traefik.yaml",
        ctx=service_ctx(tmp_path, name="traefik", options=options or {}),
    )
    assert isinstance(svc, ComposeService)
    return svc


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
    assert svc.options.dns_provider == "godaddy"  # type: ignore[attr-defined]
    assert svc.options.ingress_port == 443  # type: ignore[attr-defined]
    assert svc.options.listen_port == 8080  # type: ignore[attr-defined]
    assert svc.options.listen_host == "127.0.0.1"  # type: ignore[attr-defined]


def test_health_probe_uses_the_plain_http_ping_entrypoint(tmp_path: Path) -> None:
    """The framework's probe is HTTP-only — the TLS port would never answer."""
    svc = _load(tmp_path)
    assert svc.config.health_probe_path == "/ping"


# --- rendered compose file -------------------------------------------------


def test_rendered_compose_carries_the_godaddy_credential(tmp_path: Path) -> None:
    svc = _load(
        tmp_path,
        options={"dns_api_key": "the-key", "dns_api_secret": "the-secret"},
    )
    rendered = svc._render_compose_file().read_text()
    assert 'GODADDY_API_KEY: "the-key"' in rendered
    assert 'GODADDY_API_SECRET: "the-secret"' in rendered


def test_rendered_compose_file_is_not_world_readable(tmp_path: Path) -> None:
    """The compose file carries the DNS credential."""
    svc = _load(tmp_path, options={"dns_api_key": "the-key"})
    path = svc._render_compose_file()
    assert path.stat().st_mode & 0o077 == 0


def test_rendered_compose_publishes_the_ingress_and_ping_ports(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    rendered = svc._render_compose_file().read_text()
    assert '- "443:443"' in rendered
    assert '- "127.0.0.1:8080:8080"' in rendered


def test_rendered_compose_uses_the_dns_provider_and_acme_email(tmp_path: Path) -> None:
    svc = _load(tmp_path, options={"acme_email": "admin@example.com"})
    rendered = svc._render_compose_file().read_text()
    assert "--certificatesResolvers.letsencrypt.acme.dnschallenge.provider=godaddy" in rendered
    assert "--certificatesResolvers.letsencrypt.acme.email=admin@example.com" in rendered
    assert "--certificatesResolvers.letsencrypt.acme.dnschallenge=true" in rendered
    assert "--certificatesResolvers.letsencrypt.acme.tlschallenge=false" in rendered


def test_rendered_compose_mounts_the_config_and_data_dirs(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    rendered = svc._render_compose_file().read_text()
    assert f"{tmp_path}/config:/etc/traefik" in rendered
    assert f"{tmp_path}/data/data:/data" in rendered


# --- the routes hook -------------------------------------------------------


def test_routes_hook_writes_the_dynamic_config(tmp_path: Path) -> None:
    svc = _load(tmp_path)
    svc._pending_orchestrator_config = {
        "routes": {"http": {"routers": {"r": {"rule": "Host(`a.example`)"}}}},
        "options": {"dns_provider": "godaddy"},
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
    svc._pending_orchestrator_config = {"options": {"dns_provider": "godaddy"}}
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
            {"routes": {}, "options": {"dns_api_key": "pushed-key", "acme_email": "a@b.c"}},
        ),
    )
    rendered = svc._render_compose_file().read_text()
    assert 'GODADDY_API_KEY: "pushed-key"' in rendered
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
    assert svc.options.dns_provider == "godaddy"  # type: ignore[attr-defined]
    assert not (tmp_path / "config" / "services" / "traefik.overrides.json").exists()
