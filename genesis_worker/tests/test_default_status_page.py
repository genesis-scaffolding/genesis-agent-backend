"""Smoke tests for the default status page module + service's default panel sets.

The default status page is a Streamlit script -- a real run needs an
active Streamlit context. We can't drive it end-to-end here without
spinning up the full UI, so we test what we can in isolation: the
script imports cleanly, the page renders for a mock service, and the
default panel sets are what each kind should expose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from genesis_worker.contracts import (
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
)
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import (
    DockerService,
    DockerServiceConfig,
    UvService,
    UvServiceConfig,
)
from genesis_worker.utils.services import default_status as default_status_module

# --- import smoke ----------------------------------------------------------


def test_default_status_module_imports_cleanly() -> None:
    """Importing the script module doesn't raise (no top-level Streamlit context needed)."""
    module = default_status_module
    assert hasattr(module, "render_default_status")
    assert callable(module.render_default_status)
    assert Path(module.__file__).is_file()


# --- default ui_panels ------------------------------------------------------


class _Opts(BaseModel):
    """Minimal options model."""

    public_host: str | None = None


def _docker_service(tmp_path: Path) -> DockerService:
    config = DockerServiceConfig(
        name="docker_svc",
        display_name="Docker Svc",
        description="",
        category=ServiceCategory.OTHER,
        capabilities=ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        ),
        resource_estimate=ServiceResourceEstimate(
            vram_bytes_typical=0, vram_bytes_min=0, cpu_cores_recommended=1
        ),
        options_model=_Opts,
        image_repo="example/svc",
        image_tag="latest",
        image_install_name="svc",
        container_name="docker_svc",
        listen_host="0.0.0.0",
        listen_port=12345,
        health_probe_path="/",
    )
    return DockerService(service_ctx(tmp_path, name="docker_svc"), config=config)


def _uv_service(tmp_path: Path) -> UvService:
    config = UvServiceConfig(
        name="uv_svc",
        display_name="Uv Svc",
        description="",
        category=ServiceCategory.OTHER,
        capabilities=ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        ),
        resource_estimate=ServiceResourceEstimate(
            vram_bytes_typical=0, vram_bytes_min=0, cpu_cores_recommended=1
        ),
        options_model=_Opts,
        package_name="svcpkg",
        binary_name="svc",
        command=["run"],
        listen_host="0.0.0.0",
        listen_port=9999,
    )
    return UvService(service_ctx(tmp_path, name="uv_svc"), config=config)


def test_docker_default_panels_include_container_info(tmp_path: Path) -> None:
    """Docker services get service_info + container_info + log_tail by default."""
    assert _docker_service(tmp_path).ui_panels == (
        "service_info",
        "container_info",
        "log_tail",
    )


def test_uv_default_panels_exclude_container_info(tmp_path: Path) -> None:
    """UV services skip container_info (it's docker-only)."""
    assert _uv_service(tmp_path).ui_panels == ("service_info", "log_tail")


def test_docker_config_ui_pages_is_additive(tmp_path: Path) -> None:
    """YAML ``ui.status_panels`` is additive — it adds to the docker default.

    ``service_info`` is always present (install / start / stop controls).
    The default docker set is ``(service_info, container_info,
    log_tail)``; YAML entries that aren't already in the default are
    appended in the order declared.
    """
    config = DockerServiceConfig(
        name="docker_svc",
        display_name="Docker Svc",
        description="",
        category=ServiceCategory.OTHER,
        capabilities=ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        ),
        resource_estimate=ServiceResourceEstimate(
            vram_bytes_typical=0, vram_bytes_min=0, cpu_cores_recommended=1
        ),
        options_model=_Opts,
        image_repo="example/svc",
        image_tag="latest",
        image_install_name="svc",
        container_name="docker_svc",
        listen_host="0.0.0.0",
        listen_port=12345,
        health_probe_path="/",
        ui_pages=("auth_token",),
    )
    svc = DockerService(service_ctx(tmp_path, name="docker_svc"), config=config)
    # YAML-declared panels land after ``service_info`` and before
    # the default operational info. ``configure`` doesn't appear here
    # because this config has no ``options:`` (the test uses a minimal
    # ``_Opts`` BaseModel with no OptionSpecs); configure auto-includes
    # only when ``config.option_specs`` is non-empty (ADR-036).
    assert svc.ui_panels == (
        "service_info",
        "auth_token",
        "container_info",
        "log_tail",
    )


# --- render_default_status -------------------------------------------------


def test_render_default_status_runs_for_docker_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``render_default_status`` runs end-to-end with all panel renderers stubbed."""
    import streamlit as st

    calls: list[Any] = []

    def _fake_title(text: str) -> None:
        calls.append(("title", text))

    def _fake_container(*args, **kwargs):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            calls.append(("container",))
            yield None

        return _cm()

    monkeypatch.setattr(st, "title", _fake_title)
    monkeypatch.setattr(st, "container", _fake_container)

    # Stub panels.render so it just records its inputs.
    from genesis_worker.utils.services import panels

    def _fake_render(svc_arg, panel_list, panel_config):
        calls.append(("render", tuple(panel_list)))

    monkeypatch.setattr(panels, "render", _fake_render)

    svc = _docker_service(tmp_path)
    default_status_module.render_default_status(svc)

    titles = [c for c in calls if c[0] == "title"]
    renders = [c for c in calls if c[0] == "render"]
    assert titles == [("title", "Docker Svc")]
    assert renders == [("render", svc.ui_panels)]
