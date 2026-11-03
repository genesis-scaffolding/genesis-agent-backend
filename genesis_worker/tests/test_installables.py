"""Tests for declarative installable records and generic implementations."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from genesis_worker.tests.test_install_tarball import (
    _FakeServer,
    _free_port,
    _make_tarball,
)
from genesis_worker.utils.installables import (
    DockerImageInstall,
    DockerImageRecord,
    GithubReleaseInstall,
    GithubReleaseRecord,
    InstallDirs,
    build_installable,
    derive_asset_pattern,
    match_asset,
    merge_records,
    parse_installable_record,
)


def _github_record(**overrides: object) -> GithubReleaseRecord:
    values: dict[str, object] = {
        "name": "llama-server-test",
        "repo": "owner/repo",
        "asset_pattern": "llama-*-bin-ubuntu-x64.tar.gz",
    }
    values.update(overrides)
    return GithubReleaseRecord.model_validate(values)


def _docker_record(**overrides: object) -> DockerImageRecord:
    values: dict[str, object] = {
        "name": "engine-image",
        "image": "ghcr.io/example/engine",
    }
    values.update(overrides)
    return DockerImageRecord.model_validate(values)


def test_record_union_parses_github_release_kind() -> None:
    record = parse_installable_record(
        {
            "kind": "github_release",
            "name": "fork",
            "repo": "owner/repo",
            "asset_pattern": "*.tar.gz",
        }
    )
    assert isinstance(record, GithubReleaseRecord)
    assert record.kind == "github_release"


def test_record_union_parses_docker_image_kind() -> None:
    record = parse_installable_record(
        {
            "kind": "docker_image",
            "name": "engine",
            "image": "ghcr.io/example/engine",
        }
    )
    assert isinstance(record, DockerImageRecord)
    assert record.kind == "docker_image"


def test_record_union_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        parse_installable_record(
            {
                "kind": "maven_artifact",
                "name": "unknown",
            }
        )


def test_merge_records_keeps_seed_order() -> None:
    seeds = [_github_record(name="one"), _github_record(name="two")]
    merged = merge_records(seeds, [])
    assert [record.name for record in merged] == ["one", "two"]


def test_merge_records_override_replaces_seed() -> None:
    seed = _github_record(name="one", label="seed")
    override = _github_record(name="one", label="user")
    merged = merge_records([seed], [override])
    assert merged == [override]
    assert seed.label == "seed"


def test_merge_records_disabled_override_hides_seed() -> None:
    seed = _github_record(name="one")
    override = _github_record(name="one", enabled=False)
    merged = merge_records([seed], [override])
    assert len(merged) == 1
    assert merged[0].enabled is False


def test_merge_records_appends_unknown_name() -> None:
    seed = _github_record(name="one")
    extra = _github_record(name="two")
    merged = merge_records([seed], [extra])
    assert [record.name for record in merged] == ["one", "two"]


def test_build_installable_returns_github_release_install_for_github_kind(
    tmp_path: Path,
) -> None:
    dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
    installable = build_installable(_github_record(), dirs)
    assert isinstance(installable, GithubReleaseInstall)
    assert installable.name == "llama-server-test"


def test_build_installable_returns_docker_image_install_for_docker_kind(
    tmp_path: Path,
) -> None:
    dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
    installable = build_installable(_docker_record(), dirs)
    assert isinstance(installable, DockerImageInstall)
    assert installable.name == "engine-image"


def test_build_installable_rejects_unknown_kind(tmp_path: Path) -> None:
    class UnknownRecord:
        kind = "unknown"

    dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
    with pytest.raises(ValueError, match="unsupported installable kind"):
        build_installable(UnknownRecord(), dirs)  # type: ignore[arg-type]


def test_match_asset_matches_glob_case_insensitively() -> None:
    assert match_asset({"name": "LLAMA-B123-BIN-UBUNTU-X64.TAR.GZ"}, "llama-*.tar.gz") is True


def test_derive_asset_pattern_replaces_release_tag() -> None:
    assert (
        derive_asset_pattern("llama-b123-bin-ubuntu-x64.tar.gz", "b123")
        == "llama-*-bin-ubuntu-x64.tar.gz"
    )


def test_derive_asset_pattern_falls_back_when_tag_absent() -> None:
    assert derive_asset_pattern("engine-linux-x64.tar.gz", "v1") == "*engine-linux-x64.tar.gz"


def test_github_release_install_prefers_root_binary_over_nested_binary(tmp_path: Path) -> None:
    dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
    installable = build_installable(_github_record(), dirs)
    assert isinstance(installable, GithubReleaseInstall)
    version = "v1"
    install_root = installable._layout.installs_root / version  # noqa: SLF001
    root_binary = install_root / "llama-server"
    root_binary.parent.mkdir(parents=True)
    root_binary.write_text("#!/bin/sh\n")
    nested = install_root / "nested" / "llama-server"
    nested.parent.mkdir(parents=True)
    nested.write_text("#!/bin/sh\n")
    installable._layout.set_current_symlink(version)  # noqa: SLF001
    assert installable.binary_path() == root_binary


def test_github_release_install_resolves_nested_binary(tmp_path: Path) -> None:
    dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
    installable = build_installable(_github_record(), dirs)
    assert isinstance(installable, GithubReleaseInstall)
    version = "v1"
    install_root = installable._layout.installs_root / version  # noqa: SLF001
    nested = install_root / "cuda-12.8" / "bin"
    nested.mkdir(parents=True)
    binary = nested / "llama-server"
    binary.write_text("#!/bin/sh\n")
    installable._layout.set_current_symlink(version)  # noqa: SLF001
    assert installable.binary_path() == binary


@pytest.mark.integration
def test_github_release_install_available_versions_filters_by_pattern(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    port = _free_port()
    server = _FakeServer(port)
    try:
        server.route(
            "/repos/owner/repo/releases?per_page=50",
            json.dumps(
                [
                    {
                        "tag_name": "b123",
                        "assets": [
                            {
                                "name": "llama-b123-bin-ubuntu-x64.tar.gz",
                                "browser_download_url": f"http://127.0.0.1:{port}/asset.tar.gz",
                                "size": 42,
                            },
                            {
                                "name": "llama-b123-bin-win-x64.zip",
                                "browser_download_url": f"http://127.0.0.1:{port}/windows.zip",
                            },
                        ],
                    }
                ]
            ).encode(),
        )
        monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")
        dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
        installable = build_installable(_github_record(), dirs)
        versions = installable.available_versions()
        assert [version.version for version in versions] == ["b123"]
    finally:
        server.shutdown()


@pytest.mark.integration
def test_github_release_install_uses_checksums_asset_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib
    import json

    port = _free_port()
    server = _FakeServer(port)
    try:
        archive = _make_tarball(tmp_path, {"llama-server": b"binary"})
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        asset_url = f"http://127.0.0.1:{port}/llama-b123-bin-ubuntu-x64.tar.gz"
        server.route(
            "/repos/owner/repo/releases/latest",
            json.dumps(
                {
                    "tag_name": "b123",
                    "assets": [
                        {
                            "name": "llama-b123-bin-ubuntu-x64.tar.gz",
                            "browser_download_url": asset_url,
                            "size": archive.stat().st_size,
                        },
                        {
                            "name": "checksums.txt",
                            "browser_download_url": f"http://127.0.0.1:{port}/checksums.txt",
                        },
                    ],
                }
            ).encode(),
        )
        server.route(
            "/llama-b123-bin-ubuntu-x64.tar.gz",
            archive.read_bytes(),
            content_type="application/octet-stream",
        )
        server.route(
            "/checksums.txt",
            f"{digest}  llama-b123-bin-ubuntu-x64.tar.gz\n".encode(),
            content_type="text/plain",
        )
        monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")
        dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
        record = _github_record(checksums_asset_suffix="checksums.txt")
        installable = build_installable(record, dirs)
        assert isinstance(installable, GithubReleaseInstall)
        final = installable.install().wait()
        assert final.kind == "complete"
        manifest = installable._layout.manifest_path("b123")  # noqa: SLF001
        assert "verified: true" in manifest.read_text()
    finally:
        server.shutdown()


def test_docker_image_install_lists_registry_tags(tmp_path: Path, monkeypatch) -> None:
    from genesis_worker.utils.installables import docker_image

    monkeypatch.setattr(
        docker_image,
        "list_image_tags",
        lambda repo, *, auth_token=None: ["v2", "v1"],
    )
    dirs = InstallDirs(tmp_path / "data", tmp_path / "cache", tmp_path / "state")
    installable = build_installable(_docker_record(tag_pattern="v*"), dirs)
    assert isinstance(installable, DockerImageInstall)
    assert [version.version for version in installable.available_versions()] == ["v2", "v1"]
