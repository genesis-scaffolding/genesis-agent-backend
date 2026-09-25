"""Tests for the OverridesStore."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.contracts.host import CpuDevice, GpuDevice
from genesis_worker.services.llama_swap.overrides import OverridesStore


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = OverridesStore(tmp_path / "overrides.yaml")
    assert store.load() == {}


def test_round_trip(tmp_path: Path) -> None:
    store = OverridesStore(tmp_path / "overrides.yaml")
    data = {
        "some-entry": {"sampling": {"temp": 0.6}, "reasoning_budget": 8192},
    }
    store.save(data)
    assert store.load() == data


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "dir" / "overrides.yaml"
    store = OverridesStore(nested)
    store.save({"x": {"temp": 0.5}})
    assert nested.is_file()


def test_empty_file_loads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "overrides.yaml"
    path.write_text("")
    store = OverridesStore(path)
    assert store.load() == {}


def test_save_serializes_gpu_device_to_yaml_friendly_dict(tmp_path: Path) -> None:
    """GpuDevice instances must not reach yaml.safe_dump raw.

    The bug that prompted this test: PyYAML raised RepresenterError
    on ``yaml.safe_dump({"device": GpuDevice(...)})``. The store now
    serialises ComputeDevice subclasses to plain dicts on save.
    """
    store = OverridesStore(tmp_path / "overrides.yaml")
    intel = GpuDevice(vendor="intel", index=0, label="Vulkan device 0")
    store.save({"my-model": {"device": intel}})
    # No exception → yaml.safe_dump worked. The loaded value is a dict
    # (the discriminator shape: presence of ``vendor`` for GpuDevice).
    loaded = store.load()
    assert loaded == {
        "my-model": {"device": {"vendor": "intel", "index": 0, "label": "Vulkan device 0"}}
    }


def test_save_serializes_cpu_device_to_empty_dict(tmp_path: Path) -> None:
    """CpuDevice → ``{}`` on save (no vendor field = CPU discriminator)."""
    store = OverridesStore(tmp_path / "overrides.yaml")
    store.save({"my-model": {"device": CpuDevice()}})
    loaded = store.load()
    assert loaded == {"my-model": {"device": {}}}


def test_save_serializes_mixed_gpu_and_scalar_values(tmp_path: Path) -> None:
    """Scalar values pass through unchanged alongside serialised devices."""
    store = OverridesStore(tmp_path / "overrides.yaml")
    store.save(
        {
            "my-model": {
                "device": GpuDevice(vendor="amd", index=1, label="Radeon 2"),
                "parallel": 4,
                "kv_cache": "q8_0",
            }
        }
    )
    loaded = store.load()
    assert loaded == {
        "my-model": {
            "device": {"vendor": "amd", "index": 1, "label": "Radeon 2"},
            "parallel": 4,
            "kv_cache": "q8_0",
        }
    }
