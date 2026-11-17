"""Declared service ports and canonical names (ADR-049).

Covers the declaration matrix (the loader's validation rules), the
derivation (the ``ports:`` block becomes the runtime port fields), and
the contract surface (the canonical name and the routable ports).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import ComposeService, DockerService, load_service_spec
from genesis_worker.utils.services.spec import (
    ComposeServiceSpec,
    DockerServiceSpec,
    UvServiceSpec,
)

_CAPABILITIES = {
    "can_generate_config": False,
    "can_export_for_agent": False,
    "can_serve_llm": False,
    "can_serve_image": False,
    "can_train_models": False,
    "has_web_ui": True,
    "can_install": True,
}

_RESOURCE_ESTIMATE = {
    "vram_bytes_typical": 0,
    "vram_bytes_min": 0,
    "cpu_cores_recommended": 1,
}

DOCKER_BASE: dict[str, Any] = {
    "version": 1,
    "kind": "docker",
    "name": "demo",
    "display_name": "Demo",
    "description": "demo service",
    "category": "utility",
    "capabilities": dict(_CAPABILITIES),
    "resource_estimate": dict(_RESOURCE_ESTIMATE),
    "image": {"repo": "example/demo", "tag": "v1"},
    "container": {"name": "demo", "listen_port": 8080, "health_probe_path": "/health"},
}

UV_BASE: dict[str, Any] = {
    "version": 1,
    "kind": "uv",
    "name": "uv_demo",
    "display_name": "UV Demo",
    "description": "uv service",
    "category": "utility",
    "capabilities": dict(_CAPABILITIES),
    "resource_estimate": dict(_RESOURCE_ESTIMATE),
    "package_name": "uvdemo",
    "binary_name": "uvdemo",
    "command": ["run"],
    "listen_port": 9999,
}

COMPOSE_BASE: dict[str, Any] = {
    "version": 1,
    "kind": "compose",
    "name": "stack",
    "display_name": "Stack",
    "description": "compose service",
    "category": "utility",
    "capabilities": dict(_CAPABILITIES),
    "resource_estimate": dict(_RESOURCE_ESTIMATE),
    "compose_template": (
        'version: "3.8"\n'
        "services:\n"
        "  app:\n"
        "    image: example/demo\n"
        "    ports:\n"
        '      - "0.0.0.0:3000:3000"\n'
        '      - "0.0.0.0:8765:8765"\n'
    ),
    "options": {
        "listen_port": {"type": "port", "default": 3000},
        "api_port": {"type": "port", "default": 8765},
    },
}


def _write_yaml(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload))
    return path


def _docker(**overrides: Any) -> dict[str, Any]:
    payload = copy.deepcopy(DOCKER_BASE)
    payload.update(copy.deepcopy(overrides))
    return payload


def _uv(**overrides: Any) -> dict[str, Any]:
    payload = copy.deepcopy(UV_BASE)
    payload.update(copy.deepcopy(overrides))
    return payload


def _compose(**overrides: Any) -> dict[str, Any]:
    payload = copy.deepcopy(COMPOSE_BASE)
    payload.update(copy.deepcopy(overrides))
    return payload


# --- the declaration matrix -------------------------------------------------


def test_exactly_one_primary_is_required() -> None:
    """A block with two primaries is rejected."""
    with pytest.raises(ValueError, match="primary ports"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo"},
                ports=[
                    {"label": "ui", "primary": True, "host": 3000},
                    {"label": "api", "primary": True, "host": 8765},
                ],
            )
        )


def test_a_block_without_a_primary_is_rejected() -> None:
    """A block whose ports are all secondary is rejected."""
    with pytest.raises(ValueError, match="primary ports"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo"},
                ports=[
                    {"label": "ui", "primary": False, "host": 3000},
                    {"label": "api", "primary": False, "host": 8765},
                ],
            )
        )


def test_duplicate_port_labels_are_rejected() -> None:
    """Two ports of one service may not share a label."""
    with pytest.raises(ValueError, match="port label 'api' twice"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo"},
                ports=[
                    {"label": "ui", "primary": True, "host": 3000},
                    {"label": "api", "primary": False, "host": 8765},
                    {"label": "api", "primary": False, "host": 8766},
                ],
            )
        )


def test_every_port_of_a_multi_port_service_needs_a_label() -> None:
    """The labels are what keep the per-port hostnames apart."""
    with pytest.raises(ValueError, match="each needs a label"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo"},
                ports=[
                    {"label": "ui", "primary": True, "host": 3000},
                    {"primary": False, "host": 8765},
                ],
            )
        )


def test_a_port_label_must_be_a_dns_label() -> None:
    """A label ends up in a hostname, so it must be one."""
    with pytest.raises(ValueError, match="not a DNS label"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo"},
                ports=[
                    {"label": "UI", "primary": True, "host": 3000},
                    {"label": "api", "primary": False, "host": 8765},
                ],
            )
        )


def test_duplicate_host_ports_are_rejected() -> None:
    """One service may not claim the same host port twice."""
    with pytest.raises(ValueError, match="host port 8765 twice"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo"},
                ports=[
                    {"label": "ui", "primary": True, "host": 3000},
                    {"label": "api", "primary": False, "host": 8765},
                    {"label": "grpc", "primary": False, "host": 8765},
                ],
            )
        )


def test_the_uv_kind_declares_at_most_one_port() -> None:
    """The uv runner publishes one port."""
    payload = _uv()
    payload.pop("listen_port")
    with pytest.raises(ValueError, match="the uv kind publishes one port"):
        UvServiceSpec.model_validate(
            payload
            | {
                "ports": [
                    {"label": "ui", "primary": True, "host": 3000},
                    {"label": "api", "primary": False, "host": 8765},
                ]
            }
        )


def test_ports_and_the_legacy_fields_are_mutually_exclusive() -> None:
    """Declaring the ports in two places is a load-time error."""
    with pytest.raises(ValueError, match="declare the ports in one place"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo", "listen_port": 8080},
                ports=[{"label": "ui", "primary": True, "host": 3000}],
            )
        )


def test_the_uv_legacy_listen_port_conflicts_with_the_block() -> None:
    """The uv kind's top-level ``listen_port`` must be absent too."""
    with pytest.raises(ValueError, match="declare the ports in one place"):
        UvServiceSpec.model_validate(_uv(ports=[{"label": "ui", "primary": True, "host": 3000}]))


def test_the_compose_legacy_listen_port_conflicts_with_the_block() -> None:
    """The compose kind's top-level ``listen_port`` must be absent too."""
    with pytest.raises(ValueError, match="declare the ports in one place"):
        ComposeServiceSpec.model_validate(
            _compose(
                listen_port=3000,
                ports=[{"label": "ui", "primary": True, "host": 3000}],
            )
        )


def test_an_invalid_canonical_name_is_rejected() -> None:
    """The canonical label ends up in a hostname, so it must be one."""
    with pytest.raises(ValueError, match="not a DNS label"):
        DockerServiceSpec.model_validate(
            _docker(container={"name": "demo", "listen_port": 8080}, canonical_name="Genesis")
        )


def test_a_docker_port_with_a_marker_host_needs_a_container_port() -> None:
    """The container port cannot default to an allocated host port."""
    with pytest.raises(ValueError, match="without a container port"):
        DockerServiceSpec.model_validate(
            _docker(
                container={"name": "demo"},
                ports=[{"label": "ui", "primary": True, "host": "$auto_port.listen"}],
            )
        )


def test_an_empty_ports_block_is_rejected() -> None:
    """A block with no ports declares nothing."""
    with pytest.raises(ValueError, match="empty `ports:` block"):
        DockerServiceSpec.model_validate(_docker(container={"name": "demo"}, ports=[]))


# --- the derivation ----------------------------------------------------------


def test_the_primary_becomes_the_listen_port(tmp_path: Path) -> None:
    """The primary port is the runtime port."""
    path = _write_yaml(
        tmp_path,
        "demo.yaml",
        _docker(
            container={"name": "demo"},
            ports=[
                {"label": "ui", "primary": True, "host": 3000, "container": 3000},
                {"label": "api", "primary": False, "host": 8765, "container": 8765},
            ],
        ),
    )
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert isinstance(svc, DockerService)
    assert svc.config.listen_port == 3000
    assert svc.config.internal_port == 3000
    assert len(svc.config.extra_ports) == 1
    assert svc.config.extra_ports[0].container_port == 8765
    assert svc.config.extra_ports[0].host == 8765


def test_the_legacy_form_yields_one_implicit_port(tmp_path: Path) -> None:
    """A spec without a ``ports:`` block declares one port, unlabelled."""
    path = _write_yaml(tmp_path, "demo.yaml", _docker())
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert isinstance(svc, DockerService)
    assert svc.config.ports == ()
    assert svc.canonical_name() is None
    assert svc.declared_host_ports() == (8080,)


def test_the_legacy_form_reports_one_port_on_the_contract(tmp_path: Path) -> None:
    """The framework sees one port either way."""
    path = _write_yaml(tmp_path, "demo.yaml", _docker())
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    ports = svc.service_ports()
    assert len(ports) == 1
    assert ports[0].label is None
    assert ports[0].primary is True


def test_a_declared_block_reports_its_ports_on_the_contract(tmp_path: Path) -> None:
    """Each declared port carries its label and its primary flag."""
    path = _write_yaml(
        tmp_path,
        "demo.yaml",
        _docker(
            container={"name": "demo"},
            ports=[
                {"label": "ui", "primary": True, "host": 3000, "container": 3000},
                {"label": "api", "primary": False, "host": 8765, "container": 8765},
            ],
        ),
    )
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    ports = svc.service_ports()
    assert [(port.label, port.primary) for port in ports] == [
        ("ui", True),
        ("api", False),
    ]


def test_the_canonical_name_reaches_the_contract(tmp_path: Path) -> None:
    """The declared label is what the orchestrator reads."""
    path = _write_yaml(
        tmp_path,
        "demo.yaml",
        _docker(container={"name": "demo", "listen_port": 8080}, canonical_name="demo"),
    )
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert svc.canonical_name() == "demo"


def test_auto_port_markers_inside_the_block_are_use_sites(tmp_path: Path) -> None:
    """A marker in ``ports[].host`` is a port use site of its own."""
    path = _write_yaml(
        tmp_path,
        "demo.yaml",
        _docker(
            container={"name": "demo"},
            ports=[
                {
                    "label": "ui",
                    "primary": True,
                    "host": "$auto_port.ui",
                    "container": 3000,
                },
                {
                    "label": "api",
                    "primary": False,
                    "host": "$auto_port.api",
                    "container": 8765,
                },
            ],
        ),
    )
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert svc.declared_port_markers() == ("ui", "api")
    # A marker is not a fixed port: the allocator must not reserve it.
    assert svc.declared_host_ports() == ()


def test_the_effective_port_of_a_marker_backed_port_is_resolved_at_call_time(
    tmp_path: Path,
) -> None:
    """The endpoint follows the allocation, not the load-time value."""
    path = _write_yaml(
        tmp_path,
        "demo.yaml",
        _docker(
            container={"name": "demo"},
            ports=[
                {
                    "label": "ui",
                    "primary": True,
                    "host": "$auto_port.ui",
                    "container": 3000,
                }
            ],
        ),
    )
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))
    assert svc.service_ports()[0].endpoint is None


def test_the_compose_template_must_publish_the_declared_ports(tmp_path: Path) -> None:
    """The template is the mechanism; the block declares what it publishes."""
    path = _write_yaml(
        tmp_path,
        "stack.yaml",
        _compose(
            ports=[
                {"label": "ui", "primary": True, "host": "$options.listen_port", "container": 3000},
                {"label": "api", "primary": False, "host": "$options.api_port", "container": 8765},
            ]
        ),
    )
    svc = load_service_spec(path, ctx=service_ctx(tmp_path, name="stack"))
    assert isinstance(svc, ComposeService)
    assert svc.config.listen_port == 3000
    assert [(port.label, port.host) for port in svc.config.ports] == [
        ("ui", 3000),
        ("api", 8765),
    ]


def test_a_compose_template_that_does_not_publish_a_declared_port_is_rejected(
    tmp_path: Path,
) -> None:
    """A declared port the template never publishes is a spec error."""
    path = _write_yaml(
        tmp_path,
        "stack.yaml",
        _compose(
            ports=[
                {"label": "ui", "primary": True, "host": "$options.listen_port", "container": 3000},
                {"label": "api", "primary": False, "host": 9999, "container": 9999},
            ]
        ),
    )
    with pytest.raises(ValueError, match="compose template"):
        load_service_spec(path, ctx=service_ctx(tmp_path, name="stack"))
