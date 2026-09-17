"""Session-level isolation + unit-test host mocking.

Two responsibilities:

1. Redirect XDG paths to session-scoped tmpdirs so ``Settings()``
   construction doesn't read user-overrides.env or other state from
   the developer's real install (ADR-034).
2. Stub host probes (``collect_host_info``, ``DockerContainer`` probes,
   ``collect_metrics``) so unit tests never touch the real network,
   subprocess, or hardware. Only tests marked
   ``@pytest.mark.integration`` are allowed to opt out.

The probes are replaced on the module / class level — the registries
import them via ``from X import Y`` and re-evaluate on each call, so
the patched reference is what the production code sees. Tests that
import the function at module top (e.g. ``test_host_info.py``) keep
their captured original reference and exercise the real path.
"""

from __future__ import annotations

import os

import pytest

from genesis_worker.contracts.host import HostInfo
from genesis_worker.utils.collectors import host_info as _host_info_module
from genesis_worker.utils.collectors import metrics as _metrics_module
from genesis_worker.utils.models import MachineMetrics
from genesis_worker.utils.process.docker import DockerContainer

_OVERRIDE_KEYS = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
)


@pytest.fixture(autouse=True, scope="session")
def _isolate_xdg_paths(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Point XDG base dirs at session-scoped tmpdirs for the whole test run."""
    for key in _OVERRIDE_KEYS:
        os.environ[key] = str(tmp_path_factory.mktemp(f"xdg-{key.lower()}"))


# ---------------------------------------------------------------------------
# Host / docker / metrics stubs (autouse, function-scoped)
# ---------------------------------------------------------------------------
#
# Each fixture is function-scoped so per-test monkeypatches chain on top
# of these. The session-scoped XDG isolation above has to stay session-
# scoped because it's a process-env change.

_STUB_HOST_INFO = HostInfo.empty()


def _stub_metrics() -> MachineMetrics:
    """Deterministic metrics — tests can rely on exact values if they want."""
    return MachineMetrics(
        cpu_percent=0.0,
        ram_used_gb=0.0,
        ram_total_gb=1.0,
        gpu_percent=None,
        vram_used_gb=None,
        vram_total_gb=None,
    )


@pytest.fixture(autouse=True)
def _stub_collect_host_info(request, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``collect_host_info`` so unit tests don't hit ipify / tailscale.

    The real implementation does an HTTPS roundtrip to api.ipify.org and
    a ``tailscale ip`` subprocess. Either is fine in isolation but multiplies
    across every plugin context a worker constructs (~6-8 calls per
    GenesisWorker()) — that's where the per-test wall time goes.

    Skipped for ``@pytest.mark.integration`` tests, which exercise the
    real implementation by design.
    """
    if "integration" in request.keywords:
        return
    monkeypatch.setattr(_host_info_module, "collect_host_info", lambda: _STUB_HOST_INFO)


@pytest.fixture(autouse=True)
def _stub_docker_probes(request, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every docker probe report "no" without spawning a subprocess.

    ``image_present`` fires 4× per worker construction (one per DockerService)
    during the bootstrap enabled-set walk; ``is_running`` fires on every
    ``disable()``. Both would otherwise call ``docker`` and either hit the
    daemon or — on a CI runner without docker — raise FileNotFoundError
    and crash the test.

    The staticmethod wrapping matches what ``monkeypatch.setattr`` does to
    a regular method: the patched object is a plain function, so it must
    not receive an implicit ``self``.

    Skipped for ``@pytest.mark.integration`` tests.
    """
    if "integration" in request.keywords:
        return
    monkeypatch.setattr(DockerContainer, "image_present", staticmethod(lambda image: False))
    # ``is_running(self)`` is a regular method on the class; the stub
    # must keep the same signature so the descriptor protocol binds
    # ``self`` when accessed via an instance. ``staticmethod()`` would
    # strip ``self`` and the call would raise ``TypeError``.
    monkeypatch.setattr(DockerContainer, "is_running", lambda self: False)


@pytest.fixture(autouse=True)
def _stub_collect_metrics(request, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``collect_metrics`` so unit tests don't block on nvmlInit.

    The real implementation calls ``pynvml.nvmlInit()`` which blocks ~1s
    even when no NVIDIA driver is loaded (a graceful-degradation path that
    still costs the wait). Unit tests don't care about live metrics; the
    few tests that do already mock or override the function locally.

    Skipped for ``@pytest.mark.integration`` tests.
    """
    if "integration" in request.keywords:
        return
    monkeypatch.setattr(_metrics_module, "collect_metrics", _stub_metrics)
