"""The migration case: a service that was running before its spec adopted the marker.

ADR-043 wrote the allocation record only on the start path. A container that
was already running when its spec adopted ``$auto_port.<name>`` therefore had
a running container and no record, and the framework reported ``starting``
with no endpoint forever.

ADR-045 closes that hole: the running container is the truth, and the worker
adopts the port it publishes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genesis_worker.contracts import ServiceState
from genesis_worker.registries import ServiceRegistry
from genesis_worker.settings import PathsSettings, Settings
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.ports.allocator import NodePortAllocator
from genesis_worker.utils.process.docker import ContainerPortBinding
from genesis_worker.utils.services import DockerService, load_service_spec

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


def _metube_bindings(*, running: bool = True) -> list[ContainerPortBinding]:
    """A snapshot of the node: metube publishes its container port 8081 on 8098."""
    return [
        ContainerPortBinding(
            name="metube",
            host_ports=(8098,),
            running=running,
            port_map=((8081, 8098),),
        )
    ]


def _allocator(tmp_path: Path) -> NodePortAllocator:
    """A hermetic allocator — no docker daemon, no real bind."""
    return NodePortAllocator(
        tmp_path / "state",
        fixed_ports=lambda: set(),
        container_bindings=list,
        bind_probe=lambda port: True,
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(paths=PathsSettings(state_dir=tmp_path / "state"))


def test_a_service_running_from_before_the_marker_migration_is_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The brief's scenario: marker default, running container, no record."""
    ctx = service_ctx(tmp_path, name="metube", port_allocator=_allocator(tmp_path))
    svc = load_service_spec(_DECLARATIVE_DIR / "metube.yaml", ctx=ctx)
    assert isinstance(svc, DockerService)
    assert svc.effective_host_port() is None

    # The worker observes the running container and adopts its port.
    assert svc.port_binder().adopt("listen", 8098) is True
    assert svc.effective_host_port() == 8098

    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: True,
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.HealthProbe.probe", lambda self: True
    )
    status = svc.status()
    assert status.state == ServiceState.RUNNING
    assert status.endpoint == "http://127.0.0.1:8098/"

    # The record is persisted — the next process sees it.
    second = NodePortAllocator(
        tmp_path / "state",
        fixed_ports=lambda: set(),
        container_bindings=list,
        bind_probe=lambda port: True,
    )
    assert second.allocated("metube", "listen") == 8098


def test_a_marker_service_with_no_container_is_stopped_not_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No container, no port — the status is STOPPED, not a hopeful STARTING."""
    ctx = service_ctx(tmp_path, name="metube", port_allocator=_allocator(tmp_path))
    svc = load_service_spec(_DECLARATIVE_DIR / "metube.yaml", ctx=ctx)
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: False,
    )
    status = svc.status()
    assert status.state == ServiceState.STOPPED


def test_the_registry_adopts_at_construction(tmp_path: Path, monkeypatch) -> None:
    """The pass runs when the registry is built."""
    monkeypatch.setattr(
        "genesis_worker.registries.container_port_bindings", lambda: _metube_bindings()
    )
    ServiceRegistry(_settings(tmp_path))
    data = yaml.safe_load((tmp_path / "state" / "port_allocations.yaml").read_text())
    assert data == {"allocations": {"metube": {"listen": 8098}}}


def test_the_registry_adopts_after_a_rebuild(tmp_path: Path, monkeypatch) -> None:
    """A rebuilt spec adopts the published port too."""
    monkeypatch.setattr(
        "genesis_worker.registries.container_port_bindings", lambda: _metube_bindings()
    )
    monkeypatch.setattr(
        "genesis_worker.utils.services.docker_service.DockerContainer.is_running",
        lambda self: False,
    )
    registry = ServiceRegistry(_settings(tmp_path))
    registry.rebuild("metube")
    data = yaml.safe_load((tmp_path / "state" / "port_allocations.yaml").read_text())
    assert data == {"allocations": {"metube": {"listen": 8098}}}


def test_the_adoption_pass_can_be_switched_off(tmp_path: Path, monkeypatch) -> None:
    """``adopt_published_ports=False`` writes nothing."""
    monkeypatch.setattr(
        "genesis_worker.registries.container_port_bindings", lambda: _metube_bindings()
    )
    ServiceRegistry(_settings(tmp_path), adopt_published_ports=False)
    assert not (tmp_path / "state" / "port_allocations.yaml").exists()


def test_a_stopped_container_is_not_adopted(tmp_path: Path, monkeypatch) -> None:
    """A stopped container declares its bindings but holds none of them."""
    monkeypatch.setattr(
        "genesis_worker.registries.container_port_bindings",
        lambda: _metube_bindings(running=False),
    )
    ServiceRegistry(_settings(tmp_path))
    assert not (tmp_path / "state" / "port_allocations.yaml").exists()
