"""Tests for the Manifest dataclass and YAML round-trip."""

from __future__ import annotations

from pathlib import Path

from genesis_worker.utils.install import Manifest, ManifestSource


def test_round_trip(tmp_path: Path) -> None:
    m = Manifest(
        name="llama-swap",
        version="v0.4.5",
        source=ManifestSource(url="https://example.com/release.tar.gz"),
        sha256="abc123",
        verified=True,
        fetched_at="2026-01-15T10:00:00Z",
        size_bytes=12345678,
        install_method="github_release_tarball",
    )
    path = tmp_path / "MANIFEST"
    m.to_yaml(path)
    reloaded = Manifest.from_yaml(path)
    assert reloaded == m


def test_optional_fields_default(tmp_path: Path) -> None:
    minimal = Manifest(
        name="llama-swap",
        version="v0.4.5",
        source=ManifestSource(url="https://example.com/release.tar.gz"),
    )
    path = tmp_path / "MANIFEST"
    minimal.to_yaml(path)
    reloaded = Manifest.from_yaml(path)
    assert reloaded.sha256 is None
    assert reloaded.verified is False
    assert reloaded.size_bytes is None
    assert reloaded.fetched_at == ""
    assert reloaded.install_method == ""


def test_unverified_serialization(tmp_path: Path) -> None:
    m = Manifest(
        name="llama-swap",
        version="v0.4.5",
        source=ManifestSource(url="https://example.com/release.tar.gz"),
        sha256=None,
        verified=False,
    )
    path = tmp_path / "MANIFEST"
    m.to_yaml(path)
    reloaded = Manifest.from_yaml(path)
    assert reloaded.sha256 is None
    assert reloaded.verified is False
