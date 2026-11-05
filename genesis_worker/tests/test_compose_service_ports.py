"""The compose chain end to end (ADR-043).

Gen's chain B, verbatim: the compose template references an option, the
option carries a ``$auto_port.<name>`` marker, and the framework allocates a
port when the stack starts.

Every probe the allocator uses is injected, so the tests never touch the
docker daemon and never bind a real port.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.ports.allocator import NodePortAllocator
from genesis_worker.utils.services import ComposeService, load_service_spec

_TEMPLATE = (
    'version: "3.8"\n'
    "services:\n"
    "  app:\n"
    "    image: $images.app\n"
    "    ports:\n"
    '      - "$options.listen_host:$options.listen_port:3000"\n'
    '      - "$options.listen_host:$options.api_port:8765"\n'
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

_RESOURCE = {"vram_bytes_typical": 0, "vram_bytes_min": 0, "cpu_cores_recommended": 1}


def _spec(*, listen_port: Any, api_port: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "kind": "compose",
        "name": "demo",
        "display_name": "Demo",
        "description": "demo service",
        "category": "utility",
        "capabilities": _CAPABILITIES,
        "resource_estimate": _RESOURCE,
        "compose_template": _TEMPLATE,
        "images": [{"name": "app", "repo": "example/app", "default_tag": "latest"}],
        "options": {
            "listen_host": {"type": "string", "default": "0.0.0.0"},
            "listen_port": {"type": "port", "default": listen_port},
            "api_port": {"type": "port", "default": api_port},
        },
    }


def _allocator(tmp_path: Path) -> NodePortAllocator:
    return NodePortAllocator(
        tmp_path / "state",
        fixed_ports=lambda: set(),
        container_bindings=list,
        bind_probe=lambda port: True,
    )


def _load(tmp_path: Path, payload: dict[str, Any]) -> ComposeService:
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(payload))
    ctx = service_ctx(tmp_path, name="demo", port_allocator=_allocator(tmp_path))
    svc = load_service_spec(path, ctx=ctx)
    assert isinstance(svc, ComposeService)
    return svc


def test_the_marker_survives_the_load_time_rounds(tmp_path: Path) -> None:
    """The stored template carries both markers; nothing is allocated at load."""
    svc = _load(tmp_path, _spec(listen_port="$auto_port.listen", api_port="$auto_port.api"))
    assert svc.config.listen_port == "$auto_port.listen"
    assert "$auto_port.listen" in svc.config.compose_template
    assert "$auto_port.api" in svc.config.compose_template
    assert svc.declared_port_markers() == ("api", "listen")


def test_the_rendered_template_carries_two_different_allocated_ports(tmp_path: Path) -> None:
    svc = _load(tmp_path, _spec(listen_port="$auto_port.listen", api_port="$auto_port.api"))
    rendered = svc._rendered_template(ports=svc.port_binder().resolver())
    assert '"0.0.0.0:18000:3000"' in rendered
    assert '"0.0.0.0:18001:8765"' in rendered
    assert "$auto_port." not in rendered


def test_the_runtime_port_follows_the_allocation(tmp_path: Path) -> None:
    svc = _load(tmp_path, _spec(listen_port="$auto_port.listen", api_port="$auto_port.api"))
    assert svc.effective_host_port() is None  # never started
    svc._rendered_template(ports=svc.port_binder().resolver())
    assert svc.effective_host_port() == 18000
    assert svc.declared_host_port() is None


def test_the_rendered_file_is_byte_identical_for_fixed_options(tmp_path: Path) -> None:
    """A spec with fixed ports renders exactly what it always did."""
    svc = _load(tmp_path, _spec(listen_port=3000, api_port=8765))
    path = svc._render_compose_file()
    assert path.read_text() == (
        'version: "3.8"\n'
        "services:\n"
        "  app:\n"
        "    image: example/app:latest\n"
        "    ports:\n"
        '      - "0.0.0.0:3000:3000"\n'
        '      - "0.0.0.0:8765:8765"\n'
    )


def test_the_rendered_file_carries_the_allocations(tmp_path: Path) -> None:
    svc = _load(tmp_path, _spec(listen_port="$auto_port.listen", api_port="$auto_port.api"))
    path = svc._render_compose_file(ports=svc.port_binder().resolver())
    body = path.read_text()
    assert '"0.0.0.0:18000:3000"' in body
    assert '"0.0.0.0:18001:8765"' in body


def test_a_compose_spec_that_does_not_publish_its_runtime_port_is_rejected(
    tmp_path: Path,
) -> None:
    """The probe would watch a port the stack never binds — reject at load."""
    payload = _spec(listen_port="$auto_port.listen", api_port="$auto_port.api")
    payload["compose_template"] = (
        'version: "3.8"\n'
        "services:\n"
        "  app:\n"
        "    image: $images.app\n"
        "    ports:\n"
        '      - "$options.listen_host:$options.api_port:8765"\n'
    )
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(payload))
    ctx = service_ctx(tmp_path, name="demo", port_allocator=_allocator(tmp_path))
    with pytest.raises(ValueError, match="does not publish it"):
        load_service_spec(path, ctx=ctx)
