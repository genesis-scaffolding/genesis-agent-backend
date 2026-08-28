"""Tests for the architecture-aware image-tag filter."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.services.comfyui.install import (
    ComfyUiImage,
    _matches_host_arch,
    _normalise_host_arch,
    detect_host_arch,
)

# --- arch normalisation --------------------------------------------------


def test_normalise_none_returns_none() -> None:
    assert _normalise_host_arch(None) is None


def test_normalise_empty_returns_empty() -> None:
    """Empty string is a user override meaning "no filter"."""
    assert _normalise_host_arch("") == ""


def test_normalise_x86_64() -> None:
    assert _normalise_host_arch("x86_64") == "amd64"


def test_normalise_amd64_passthrough() -> None:
    assert _normalise_host_arch("amd64") == "amd64"


def test_normalise_aarch64() -> None:
    assert _normalise_host_arch("aarch64") == "arm64"


def test_normalise_arm64_passthrough() -> None:
    assert _normalise_host_arch("arm64") == "arm64"


def test_normalise_unknown_returns_empty() -> None:
    """Unknown arch disables the filter rather than failing closed."""
    assert _normalise_host_arch("riscv64") == ""


def test_detect_host_arch_returns_known_value() -> None:
    """``detect_host_arch`` returns one of the known suffixes, possibly empty."""
    result = detect_host_arch()
    assert result in ("amd64", "arm64", "")


# --- tag matching ---------------------------------------------------------


def test_amd64_tag_matches_amd64_host() -> None:
    assert _matches_host_arch("v0.34.0-cuda-13.0-amd64", "amd64") is True


def test_arm64_tag_matches_arm64_host() -> None:
    assert _matches_host_arch("v0.34.0-cuda-13.0-arm64", "arm64") is True


def test_amd64_tag_does_not_match_arm64_host() -> None:
    assert _matches_host_arch("v0.34.0-cuda-13.0-amd64", "arm64") is False


def test_arm64_tag_does_not_match_amd64_host() -> None:
    assert _matches_host_arch("v0.34.0-cuda-13.0-arm64", "amd64") is False


def test_arch_agnostic_tag_matches_any_host() -> None:
    assert _matches_host_arch("v0.34.0", "amd64") is True
    assert _matches_host_arch("v0.34.0", "arm64") is True


def test_latest_tag_matches_any_host() -> None:
    assert _matches_host_arch("latest", "amd64") is True
    assert _matches_host_arch("latest", "arm64") is True


def test_arch_segment_in_middle_matches() -> None:
    """Arch segment can appear at the end of any version string."""
    assert _matches_host_arch("stable-v0.34.0-amd64", "amd64") is True
    assert _matches_host_arch("stable-v0.34.0-amd64", "arm64") is False


# --- available_versions integration --------------------------------------


def _make_installable(
    tmp_path: Path,
    *,
    tags: list[str],
    host_arch: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> ComfyUiImage:
    from genesis_worker.services.comfyui import install as install_mod

    inst = ComfyUiImage(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        image_repo="ghcr.io/genesis-scaffolding/comfyui-cuda",
        image_tag="v0.34.0-cuda-13.0-amd64",
        host_arch=host_arch,
    )
    # Inject canned tags (no on-disk cache, no network).
    inst.invalidate_versions_cache()
    # Patch the underlying fetcher so we don't need docker. Using
    # monkeypatch.setattr keeps the test isolated.
    monkeypatch.setattr(
        install_mod.DockerContainer,
        "list_remote_tags",
        staticmethod(lambda repo: tags),
    )
    return inst


def test_available_versions_filters_by_host_arch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # noqa: F841 — fixture isolation
    inst = _make_installable(
        tmp_path,
        tags=[
            "v0.34.0-cuda-13.0-amd64",
            "v0.34.0-cuda-13.0-arm64",
            "v0.34.0",
            "v0.33.0-cuda-12.8-amd64",
            "v0.33.0-cuda-12.8-arm64",
            "latest",
        ],
        host_arch="amd64",
        monkeypatch=monkeypatch,
    )
    versions = inst.available_versions()
    assert [v.version for v in versions] == [
        "v0.34.0-cuda-13.0-amd64",
        "v0.34.0",
        "v0.33.0-cuda-12.8-amd64",
        "latest",
    ]


def test_available_versions_filters_for_arm64(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # noqa: F841
    inst = _make_installable(
        tmp_path,
        tags=[
            "v0.34.0-cuda-13.0-amd64",
            "v0.34.0-cuda-13.0-arm64",
            "v0.34.0",
            "latest",
        ],
        host_arch="arm64",
        monkeypatch=monkeypatch,
    )
    versions = inst.available_versions()
    assert [v.version for v in versions] == [
        "v0.34.0-cuda-13.0-arm64",
        "v0.34.0",
        "latest",
    ]


def test_available_versions_no_filter_when_arch_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # noqa: F841
    inst = _make_installable(
        tmp_path,
        tags=[
            "v0.34.0-cuda-13.0-amd64",
            "v0.34.0-cuda-13.0-arm64",
            "v0.34.0",
        ],
        host_arch="",  # user override: disable filter
        monkeypatch=monkeypatch,
    )
    versions = inst.available_versions()
    assert len(versions) == 3


def test_available_versions_auto_detects_arch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``host_arch=None`` triggers ``platform.machine()`` at call time."""
    from genesis_worker.services.comfyui import install as install_mod

    # Stub platform.machine so the auto-detect path is deterministic.
    monkeypatch.setattr(install_mod.platform, "machine", lambda: "aarch64")
    inst = ComfyUiImage(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        image_repo="ghcr.io/genesis-scaffolding/comfyui-cuda",
        image_tag="v0.34.0-cuda-13.0-arm64",
        host_arch=None,  # auto-detect
    )
    monkeypatch.setattr(
        install_mod.DockerContainer,
        "list_remote_tags",
        staticmethod(
            lambda repo: ["v0.34.0-cuda-13.0-amd64", "v0.34.0-cuda-13.0-arm64"]
        ),
    )
    versions = inst.available_versions()
    assert [v.version for v in versions] == ["v0.34.0-cuda-13.0-arm64"]
