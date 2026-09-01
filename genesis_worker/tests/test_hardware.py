"""Tests for the host hardware collector.

The collector probes ``/sys/class/drm``, ``nvidia-smi``, and ``docker info``.
Each test fakes those surfaces with monkeypatched files / subprocess
calls so the collector's vendoring logic is exercised end-to-end
without touching the host system.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from genesis_worker.contracts.host import Hardware
from genesis_worker.utils.collectors import hardware
from genesis_worker.utils.collectors.hardware import collect_hardware_info


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test gets a fresh probe — the lru_cache is process-wide."""
    hardware.reset_cache()


def _fake_drm_cards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, vendors: list[tuple[str, int]]
) -> None:
    """Create ``/sys/class/drm/cardN/device/vendor`` files under ``tmp_path``.

    ``vendors`` is a list of ``(card_name, vendor_id_hex)`` pairs.
    """
    for name, vid in vendors:
        card = tmp_path / "sys" / "class" / "drm" / name / "device"
        card.mkdir(parents=True, exist_ok=True)
        (card / "vendor").write_text(f"0x{vid:04x}\n")


def _stub_proc_nvidia(monkeypatch: pytest.MonkeyPatch, exists: bool) -> None:
    """Stub ``os.path.exists`` to fake ``/proc/driver/nvidia/version``."""
    orig = hardware.os.path.exists

    def fake(p: str) -> bool:
        if p == hardware._PROC_NVIDIA_DRIVER:
            return exists
        return orig(p)

    monkeypatch.setattr(hardware.os.path, "exists", fake)


def _stub_nvidia_smi(monkeypatch: pytest.MonkeyPatch, *, count: int, rc: int = 0) -> None:
    """Stub ``subprocess.run`` for ``nvidia-smi -L``."""

    def fake_run(args: list[str], **kw: Any) -> subprocess.CompletedProcess:
        if args[:2] == ["nvidia-smi", "-L"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=rc,
                stdout="\n".join(f"GPU {i}: Test Card" for i in range(count)) + "\n",
                stderr="",
            )
        if args[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="Runtimes: runc\n", stderr=""
            )
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)


def _stub_docker_info(monkeypatch: pytest.MonkeyPatch, *, nvidia: bool) -> None:
    """Stub ``subprocess.run`` for ``docker info`` and a no-op ``nvidia-smi``."""

    def fake_run(args: list[str], **kw: Any) -> subprocess.CompletedProcess:
        if args[:2] == ["docker", "info"]:
            text = "Runtimes: nvidia runc\n" if nvidia else "Runtimes: runc\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=text, stderr="")
        if args[:2] == ["nvidia-smi", "-L"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)


def _patch_drm_glob(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Glob ``/sys/class/drm/card*`` to the tmp_path equivalent."""
    monkeypatch.setattr(
        hardware.glob,
        "glob",
        lambda pat: sorted(str(p) for p in (tmp_path / "sys" / "class" / "drm").glob("card*")),
    )


# --- vendor enumeration ---------------------------------------------------


def test_no_gpus_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    h = collect_hardware_info()
    assert h == Hardware.empty()


def test_nvidia_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x10DE), ("card1", 0x10DE)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=True)
    _stub_nvidia_smi(monkeypatch, count=2)
    _stub_docker_info(monkeypatch, nvidia=True)
    h = collect_hardware_info()
    assert h.nvidia is True
    assert h.nvidia_count == 2
    assert h.nvidia_driver_loaded is True
    assert h.nvidia_runtime is True
    assert h.amd is False
    assert h.intel_igpu is False


def test_amd_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x1002)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    _stub_docker_info(monkeypatch, nvidia=False)
    h = collect_hardware_info()
    assert h.amd is True
    assert h.amd_count == 1
    assert h.amd_vendor_id_present is True
    assert h.nvidia is False
    assert h.intel_igpu is False


def test_intel_igpu_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x8086)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    _stub_docker_info(monkeypatch, nvidia=False)
    h = collect_hardware_info()
    assert h.intel_igpu is True
    assert h.intel_count == 1
    assert h.nvidia is False
    assert h.amd is False


def test_multi_vendor_laptop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Intel iGPU + AMD discrete — common laptop config."""
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x8086), ("card1", 0x1002)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    _stub_docker_info(monkeypatch, nvidia=False)
    h = collect_hardware_info()
    assert h.intel_igpu is True
    assert h.amd is True
    assert h.nvidia is False


def test_unknown_vendor_id_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Vendor IDs we don't classify are silently skipped."""
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x1234)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    h = collect_hardware_info()
    assert h == Hardware.empty()


def test_unreadable_vendor_file_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cards with missing/permission-denied ``vendor`` file are skipped."""
    (tmp_path / "sys" / "class" / "drm" / "card0" / "device").mkdir(parents=True)
    # No vendor file written -> open() raises FileNotFoundError.
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    h = collect_hardware_info()
    assert h == Hardware.empty()


def test_garbage_vendor_id_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Non-hex content in vendor file is ignored, not crashed."""
    card = tmp_path / "sys" / "class" / "drm" / "card0" / "device"
    card.mkdir(parents=True)
    (card / "vendor").write_text("not-a-hex-id\n")
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    h = collect_hardware_info()
    assert h == Hardware.empty()


# --- nvidia-smi interaction ----------------------------------------------


def test_nvidia_smi_missing_binary_treated_as_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No ``nvidia-smi`` on PATH → counts fall back to PCI enumeration."""
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x10DE)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)

    def fake_run(args: list[str], **kw: Any) -> subprocess.CompletedProcess:
        if args[:2] == ["nvidia-smi", "-L"]:
            raise FileNotFoundError("nvidia-smi not on PATH")
        if args[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)
    h = collect_hardware_info()
    assert h.nvidia is True
    assert h.nvidia_count == 1


def test_nvidia_smi_nonzero_returns_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Driver loaded but nvidia-smi fails (e.g. NVML not initialized) → count 0."""
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x10DE)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=True)
    _stub_nvidia_smi(monkeypatch, count=0, rc=9)
    h = collect_hardware_info()
    # PCI saw the card, nvidia-smi didn't confirm it; we still know it's there.
    assert h.nvidia is True
    assert h.nvidia_count == 1
    assert h.nvidia_driver_loaded is True


def test_nvidia_smi_count_takes_precedence_over_pci_when_higher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unusual case: nvidia-smi sees more cards than PCI enumeration."""
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x10DE)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=True)
    _stub_nvidia_smi(monkeypatch, count=4)
    h = collect_hardware_info()
    assert h.nvidia_count == 4


# --- runtime probe -------------------------------------------------------


def test_nvidia_runtime_false_when_no_gpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Don't bother probing docker info on hosts with no NVIDIA."""
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    # No docker info stub — if probed, the fake would AssertionError.
    h = collect_hardware_info()
    assert h.nvidia_runtime is False


def test_nvidia_runtime_true_when_docker_has_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_drm_cards(monkeypatch, tmp_path, [("card0", 0x10DE)])
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=True)
    _stub_nvidia_smi(monkeypatch, count=1)
    _stub_docker_info(monkeypatch, nvidia=True)
    h = collect_hardware_info()
    assert h.nvidia_runtime is True


# --- caching -------------------------------------------------------------


def test_caches_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Second call doesn't re-probe."""
    _patch_drm_glob(monkeypatch, tmp_path)
    _stub_proc_nvidia(monkeypatch, exists=False)
    _stub_nvidia_smi(monkeypatch, count=0)
    first = collect_hardware_info()
    # Sabotage the probe; cached value must still come back.
    monkeypatch.setattr(
        "genesis_worker.utils.collectors.hardware.glob.glob",
        lambda pat: [],
    )
    second = collect_hardware_info()
    assert first == second


# --- vendor summary ------------------------------------------------------


def test_vendor_summary_combinations() -> None:
    assert Hardware.empty().vendor_summary() == "none detected"
    assert Hardware(nvidia=True, nvidia_count=2).vendor_summary() == "NVIDIA (2)"
    assert Hardware(amd=True, amd_count=1).vendor_summary() == "AMD (1)"
    assert Hardware(intel_igpu=True, intel_count=1).vendor_summary() == "Intel iGPU (1)"
    h = Hardware(nvidia=True, nvidia_count=1, amd=True, amd_count=1, intel_igpu=True, intel_count=1)
    assert h.vendor_summary() == "NVIDIA (1) + AMD (1) + Intel iGPU (1)"
