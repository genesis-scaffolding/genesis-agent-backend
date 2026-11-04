"""The ``$auto_port.<name>`` grammar and the use-site rule (ADR-043).

The grammar lives in one place (``utils.ports.markers``); this file pins it,
and pins the rule that one marker name means one port.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.ports.markers import (
    AUTO_PORT_KEY_PREFIX,
    assert_no_markers,
    is_marker,
    marker_name,
)
from genesis_worker.utils.services import load_service_spec

# --- the grammar ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["$auto_port.listen", "$auto_port.api", "$auto_port.port-1", "$auto_port.a_b"],
)
def test_well_formed_markers_are_recognised(value: str) -> None:
    assert is_marker(value) is True
    assert marker_name(value) == value[len("$" + AUTO_PORT_KEY_PREFIX) :]


@pytest.mark.parametrize(
    "value",
    [
        "auto_port.listen",  # no ``$``
        "$auto_port.",  # no name
        "$auto_port.listen.api",  # dotted names are not markers
        "$auto_port.listen port",  # whitespace
        "$auto_port.li$sten",
        "",
        8096,
        None,
    ],
)
def test_malformed_markers_are_rejected(value: Any) -> None:
    assert is_marker(value) is False
    assert marker_name(value) is None


def test_assert_no_markers_accepts_a_clean_payload() -> None:
    assert_no_markers({"ports": {"8081/tcp": ("0.0.0.0", 18000)}}, "port map")


def test_assert_no_markers_rejects_a_surviving_marker() -> None:
    with pytest.raises(ValueError, match=r"\$auto_port\.listen"):
        assert_no_markers({"ports": {"8081/tcp": ("0.0.0.0", "$auto_port.listen")}}, "port map")


def test_assert_no_markers_finds_a_marker_embedded_in_a_string() -> None:
    """A marker inside a compose template line is still a marker."""
    payload = 'ports:\n  - "0.0.0.0:$auto_port.listen:3000"\n'
    with pytest.raises(ValueError, match=r"\$auto_port\.listen"):
        assert_no_markers(payload, "compose template")


def test_assert_no_markers_ignores_unrelated_placeholders() -> None:
    assert_no_markers({"env": {"X": "$not_a_real_key"}}, "env")


# --- the use-site rule ------------------------------------------------------

_DOCKER_BASE: dict[str, Any] = {
    "version": 1,
    "kind": "docker",
    "name": "demo",
    "display_name": "Demo",
    "description": "demo service",
    "category": "crawler",
    "capabilities": {
        "can_generate_config": False,
        "can_export_for_agent": False,
        "can_serve_llm": False,
        "can_serve_image": False,
        "can_train_models": False,
        "has_web_ui": True,
        "can_install": True,
    },
    "resource_estimate": {
        "vram_bytes_typical": 0,
        "vram_bytes_min": 0,
        "cpu_cores_recommended": 1,
    },
    "image": {"repo": "example/demo", "tag": "v1"},
    "container": {"name": "demo", "listen_port": 8080, "internal_port": 8081},
}


def _write_yaml(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


def _load(tmp_path: Path, payload: dict):
    path = _write_yaml(tmp_path, payload)
    return load_service_spec(path, ctx=service_ctx(tmp_path, name="demo"))


def test_option_marker_is_one_use_site(tmp_path: Path) -> None:
    """An option carrying a marker declares the port; the field references it."""
    payload = dict(_DOCKER_BASE)
    payload["container"] = dict(_DOCKER_BASE["container"])
    payload["container"]["listen_port"] = "$options.listen_port"
    payload["options"] = {"listen_port": {"type": "port", "default": "$auto_port.listen"}}
    svc = _load(tmp_path, payload)
    assert svc.declared_port_markers() == ("listen",)
    assert svc.config.listen_port == "$auto_port.listen"


def test_two_options_with_the_same_marker_name_are_rejected(tmp_path: Path) -> None:
    payload = dict(_DOCKER_BASE)
    payload["options"] = {
        "web_port": {"type": "port", "default": "$auto_port.same"},
        "api_port": {"type": "port", "default": "$auto_port.same"},
    }
    with pytest.raises(ValueError, match="declared twice"):
        _load(tmp_path, payload)


def test_two_container_fields_with_the_same_marker_name_are_rejected(tmp_path: Path) -> None:
    payload = dict(_DOCKER_BASE)
    payload["container"] = dict(_DOCKER_BASE["container"])
    payload["container"]["listen_port"] = "$auto_port.web"
    payload["container"]["extra_ports"] = [{"container": 9000, "host": "$auto_port.web"}]
    with pytest.raises(ValueError, match="declared twice"):
        _load(tmp_path, payload)


def test_an_option_and_a_reference_to_it_are_accepted(tmp_path: Path) -> None:
    """The field written as ``$options.X`` references the option's use site."""
    payload = dict(_DOCKER_BASE)
    payload["container"] = dict(_DOCKER_BASE["container"])
    payload["container"]["listen_port"] = "$options.listen_port"
    payload["container"]["extra_ports"] = [{"container": 9000, "host": "$options.api_port"}]
    payload["options"] = {
        "listen_port": {"type": "port", "default": "$auto_port.listen"},
        "api_port": {"type": "port", "default": "$auto_port.api"},
    }
    svc = _load(tmp_path, payload)
    assert sorted(svc.declared_port_markers()) == ["api", "listen"]


def test_a_compose_template_literal_is_its_own_use_site(tmp_path: Path) -> None:
    """A literal marker in the template that no option declares gets its own port."""
    payload: dict[str, Any] = {
        "version": 1,
        "kind": "compose",
        "name": "demo",
        "display_name": "Demo",
        "description": "demo service",
        "category": "utility",
        "capabilities": _DOCKER_BASE["capabilities"],
        "resource_estimate": _DOCKER_BASE["resource_estimate"],
        "compose_template": (
            'services:\n  app:\n    ports:\n      - "0.0.0.0:$auto_port.db:5432"\n'
        ),
    }
    svc = _load(tmp_path, payload)
    assert svc.declared_port_markers() == ("db",)


def test_a_compose_template_literal_matching_an_option_is_not_a_second_site(
    tmp_path: Path,
) -> None:
    payload: dict[str, Any] = {
        "version": 1,
        "kind": "compose",
        "name": "demo",
        "display_name": "Demo",
        "description": "demo service",
        "category": "utility",
        "capabilities": _DOCKER_BASE["capabilities"],
        "resource_estimate": _DOCKER_BASE["resource_estimate"],
        "compose_template": (
            'services:\n  app:\n    ports:\n      - "0.0.0.0:$auto_port.db:5432"\n'
        ),
        "options": {"db_port": {"type": "port", "default": "$auto_port.db"}},
    }
    svc = _load(tmp_path, payload)
    assert svc.declared_port_markers() == ("db",)
