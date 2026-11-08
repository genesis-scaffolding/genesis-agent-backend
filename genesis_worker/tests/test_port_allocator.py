"""The node-level port allocator (ADR-043).

Every probe the allocator uses is injected, so the tests never touch the
docker daemon and never bind a real port.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genesis_worker.utils.ports.allocator import DYNAMIC_PORT_RANGE, NodePortAllocator
from genesis_worker.utils.ports.environment import ContainerPortBinding


class _Env:
    """A fake host: fixed ports, container bindings, and a bind probe."""

    def __init__(
        self,
        *,
        fixed: set[int] | None = None,
        bindings: list[ContainerPortBinding] | None = None,
        busy: set[int] | None = None,
    ) -> None:
        self.fixed = fixed or set()
        self.bindings = bindings or []
        self.busy = busy or set()

    def fixed_ports(self) -> set[int]:
        return self.fixed

    def container_bindings(self) -> list[ContainerPortBinding]:
        return self.bindings

    def bindable(self, port: int) -> bool:
        return port not in self.busy


def _allocator(tmp_path: Path, env: _Env, **kwargs) -> NodePortAllocator:
    return NodePortAllocator(
        tmp_path / "state",
        fixed_ports=env.fixed_ports,
        container_bindings=env.container_bindings,
        bind_probe=env.bindable,
        **kwargs,
    )


def test_allocate_returns_a_port_from_the_range(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    port = alloc.allocate("metube", "listen")
    assert DYNAMIC_PORT_RANGE[0] <= port <= DYNAMIC_PORT_RANGE[1]


def test_n_names_in_one_service_get_n_distinct_ports(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    ports = {alloc.allocate("metube", name) for name in ("listen", "api", "extra")}
    assert len(ports) == 3


def test_the_same_name_in_two_services_never_gets_the_same_port(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    assert alloc.allocate("metube", "listen") != alloc.allocate("other", "listen")


def test_a_fixed_port_is_skipped(tmp_path: Path) -> None:
    low = DYNAMIC_PORT_RANGE[0]
    alloc = _allocator(tmp_path, _Env(fixed={low}))
    assert alloc.allocate("metube", "listen") == low + 1


def test_a_port_declared_by_a_container_is_skipped(tmp_path: Path) -> None:
    low = DYNAMIC_PORT_RANGE[0]
    env = _Env(bindings=[ContainerPortBinding(name="other", host_ports=(low,))])
    alloc = _allocator(tmp_path, env)
    assert alloc.allocate("metube", "listen") == low + 1


def test_a_port_that_fails_the_bind_probe_is_skipped(tmp_path: Path) -> None:
    low = DYNAMIC_PORT_RANGE[0]
    env = _Env(busy={low, low + 1})
    alloc = _allocator(tmp_path, env)
    assert alloc.allocate("metube", "listen") == low + 2


def test_a_port_declared_by_our_own_container_is_reusable(tmp_path: Path) -> None:
    """Our own container publishing the port makes it free for us."""
    low = DYNAMIC_PORT_RANGE[0]
    env = _Env(
        bindings=[ContainerPortBinding(name="metube", host_ports=(low,))],
        busy={low},
    )
    alloc = _allocator(tmp_path, env)
    assert alloc.allocate("metube", "listen", own_containers=["metube"]) == low


def test_a_port_declared_by_our_own_compose_project_is_reusable(tmp_path: Path) -> None:
    low = DYNAMIC_PORT_RANGE[0]
    env = _Env(
        bindings=[
            ContainerPortBinding(
                name="genesis-productivity",
                host_ports=(low,),
                compose_project="genesis-productivity-stack",
            )
        ],
        busy={low},
    )
    alloc = _allocator(tmp_path, env)
    assert (
        alloc.allocate(
            "genesis-productivity", "listen", own_containers=["genesis-productivity-stack"]
        )
        == low
    )


def test_the_registry_file_round_trips(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    port = alloc.allocate("metube", "listen")
    path = tmp_path / "state" / "port_allocations.yaml"
    assert path.is_file()
    data = yaml.safe_load(path.read_text())
    assert data == {"allocations": {"metube": {"listen": port}}}
    assert alloc.allocated("metube", "listen") == port
    assert alloc.snapshot() == {"metube": {"listen": port}}


def test_a_second_allocator_sees_the_first_ones_allocations(tmp_path: Path) -> None:
    """The second worker process reads the same file."""
    first = _allocator(tmp_path, _Env())
    port = first.allocate("metube", "listen")
    second = _allocator(tmp_path, _Env())
    assert second.allocated("metube", "listen") == port
    assert second.allocate("metube", "listen") == port
    assert second.allocate("metube", "other") != port


def test_release_frees_a_record(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    alloc.allocate("metube", "listen")
    alloc.release("metube", "listen")
    assert alloc.allocated("metube", "listen") is None
    assert alloc.snapshot() == {}


def test_the_recorded_port_is_reused_on_the_next_allocate(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    port = alloc.allocate("metube", "listen")
    assert alloc.allocate("metube", "listen") == port


def test_a_taken_recorded_port_is_reallocated(tmp_path: Path) -> None:
    """When somebody else took the port, the record moves."""
    low = DYNAMIC_PORT_RANGE[0]
    alloc = _allocator(tmp_path, _Env())
    port = alloc.allocate("metube", "listen")
    assert port == low
    # Somebody else takes the port: a fixed port now, and the bind probe
    # says it is busy.
    taken = _Env(fixed={low}, busy={low})
    alloc = _allocator(tmp_path, taken)
    assert alloc.allocate("metube", "listen") == low + 1
    assert alloc.allocated("metube", "listen") == low + 1


def test_an_exhausted_range_raises(tmp_path: Path) -> None:
    env = _Env(busy=set(range(DYNAMIC_PORT_RANGE[0], DYNAMIC_PORT_RANGE[1] + 1)))
    alloc = _allocator(tmp_path, env)
    with pytest.raises(RuntimeError, match="exhausted"):
        alloc.allocate("metube", "listen")


def test_a_corrupt_registry_reads_as_empty(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "port_allocations.yaml").write_text("allocations: [not, a, mapping]\n")
    alloc = _allocator(tmp_path, _Env())
    assert alloc.allocated("metube", "listen") is None
    assert alloc.allocate("metube", "listen") == DYNAMIC_PORT_RANGE[0]


def test_reconcile_releases_records_the_service_no_longer_declares(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    alloc.allocate("metube", "listen")
    alloc.allocate("metube", "api")
    alloc.reconcile("metube", ["listen"])
    assert alloc.allocated("metube", "listen") is not None
    assert alloc.allocated("metube", "api") is None


# --- adoption (ADR-045) -----------------------------------------------------


def test_adopt_records_a_published_port(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    assert alloc.adopt("metube", "listen", 8098) is True
    assert alloc.allocated("metube", "listen") == 8098
    path = tmp_path / "state" / "port_allocations.yaml"
    assert yaml.safe_load(path.read_text()) == {"allocations": {"metube": {"listen": 8098}}}
    assert alloc.snapshot() == {"metube": {"listen": 8098}}


def test_adopt_does_not_overwrite_an_existing_record(tmp_path: Path) -> None:
    """A live allocation is never clobbered by an observation."""
    alloc = _allocator(tmp_path, _Env())
    alloc.adopt("metube", "listen", 8098)
    assert alloc.adopt("metube", "listen", 9000) is False
    assert alloc.allocated("metube", "listen") == 8098


def test_adopt_refuses_a_port_another_service_holds(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env())
    port = alloc.allocate("other", "listen")
    assert alloc.adopt("metube", "listen", port) is False
    assert alloc.allocated("metube", "listen") is None


def test_adopt_refuses_a_reserved_fixed_port(tmp_path: Path) -> None:
    alloc = _allocator(tmp_path, _Env(fixed={8098}))
    assert alloc.adopt("metube", "listen", 8098) is False
    assert alloc.allocated("metube", "listen") is None


def test_adopt_is_visible_to_a_second_allocator(tmp_path: Path) -> None:
    """The second worker process reads the adopted port."""
    first = _allocator(tmp_path, _Env())
    first.adopt("metube", "listen", 8098)
    second = _allocator(tmp_path, _Env())
    assert second.allocated("metube", "listen") == 8098


def test_adopting_twice_is_idempotent(tmp_path: Path) -> None:
    """Both worker processes adopt at startup — the second one changes nothing."""
    alloc = _allocator(tmp_path, _Env())
    assert alloc.adopt("metube", "listen", 8098) is True
    assert alloc.adopt("metube", "listen", 8098) is False
    assert alloc.snapshot() == {"metube": {"listen": 8098}}


def test_adopt_does_not_run_the_bind_probe(tmp_path: Path) -> None:
    """The port is already published by our own container — no bind test."""
    probed: list[int] = []
    env = _Env(busy={8098})
    alloc = NodePortAllocator(
        tmp_path / "state",
        fixed_ports=env.fixed_ports,
        container_bindings=env.container_bindings,
        bind_probe=lambda port: probed.append(port) or True,
    )
    assert alloc.adopt("metube", "listen", 8098) is True
    assert probed == []
