"""Tests for llama-swap's declarative installable records."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from genesis_worker.services.llama_swap.installables import (
    candidate_assets,
    ensure_unique_installable_name,
    fork_slug,
    guess_backend,
    load_seed_records,
    parse_repo_url,
)
from genesis_worker.services.llama_swap.service import LlamaSwapService
from genesis_worker.tests._factories import service_ctx
from genesis_worker.tests.test_install_tarball import _FakeServer, _free_port
from genesis_worker.utils.config_overrides import (
    read_service_overrides,
    service_overrides_path,
    write_service_overrides,
)
from genesis_worker.utils.installables import (
    DockerImageInstall,
    DockerImageRecord,
    GithubReleaseRecord,
    match_asset,
)


@pytest.fixture(autouse=True)
def _stub_radiance_group_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "genesis_worker.services.llama_swap.service.resolve_group_gids",
        lambda names: (),
    )


def _user_record(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "kind": "github_release",
        "name": "llama-server-prism",
        "variant": "prism",
        "backend": "vulkan",
        "repo": "PrismML-Eng/llama.cpp",
        "asset_pattern": "llama-*-bin-ubuntu-vulkan-x64.tar.gz",
        "binary_name": "llama-server",
    }
    values.update(overrides)
    return values


def _install_fake_binary(installable: object, version: str = "v1") -> Path:
    layout = installable._layout  # type: ignore[attr-defined]  # noqa: SLF001
    install_root = layout.installs_root / version
    install_root.mkdir(parents=True)
    binary = install_root / "llama-server"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    layout.set_current_symlink(version)
    return binary


def test_ensure_unique_installable_name_rejects_existing_name() -> None:
    records = load_seed_records()
    with pytest.raises(ValueError, match="already exists"):
        ensure_unique_installable_name(records, "llama-server-cpu")


def test_ensure_unique_installable_name_allows_editing_existing_record() -> None:
    records = load_seed_records()
    ensure_unique_installable_name(
        records,
        "llama-server-cpu",
        editing_name="llama-server-cpu",
    )


def test_seed_records_define_the_five_builtin_installables() -> None:
    records = load_seed_records()
    assert [record.name for record in records] == [
        "llama-swap",
        "llama-server-cuda",
        "llama-server-cpu",
        "llama-server-vulkan",
        "radiance",
    ]
    assert records[0].primary is True


def test_seed_records_include_the_radiance_docker_image() -> None:
    record = next(item for item in load_seed_records() if item.name == "radiance")
    assert isinstance(record, DockerImageRecord)
    assert record.kind == "docker_image"
    assert record.image == "stilldeadcode/radiance"
    assert record.default_tag == "latest"
    assert record.primary is False
    assert record.variant is None
    assert record.backend is None


def test_radiance_record_builds_a_docker_image_installable(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    installable = next(item for item in svc.installs() if item.name == "radiance")
    assert isinstance(installable, DockerImageInstall)
    assert installable.image_ref == "stilldeadcode/radiance:latest"


def test_seed_record_patterns_match_real_upstream_asset_names() -> None:
    patterns = {
        record.name: record.asset_pattern
        for record in load_seed_records()
        if isinstance(record, GithubReleaseRecord)
    }
    assert match_asset({"name": "llama-swap_249_linux_amd64.tar.gz"}, patterns["llama-swap"])
    assert match_asset(
        {"name": "llama.cpp-b10375-cuda-12.8-amd64.tar.gz"}, patterns["llama-server-cuda"]
    )
    assert match_asset({"name": "llama-b10375-bin-ubuntu-x64.tar.gz"}, patterns["llama-server-cpu"])
    assert match_asset(
        {"name": "llama-b10375-bin-ubuntu-vulkan-x64.tar.gz"}, patterns["llama-server-vulkan"]
    )


def test_installs_returns_one_installable_per_enabled_record(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path, options={"installables": [_user_record()]}))
    assert len(svc.installs()) == 6
    assert svc.installs()[-1].name == "llama-server-prism"


def test_installs_preserves_seed_order(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    assert [installable.name for installable in svc.installs()] == [
        "llama-swap",
        "llama-server-cuda",
        "llama-server-cpu",
        "llama-server-vulkan",
        "radiance",
    ]


def test_disabled_record_is_not_in_installs(tmp_path: Path) -> None:
    svc = LlamaSwapService(
        service_ctx(tmp_path, options={"installables": [_user_record(enabled=False)]})
    )
    assert "llama-server-prism" not in [installable.name for installable in svc.installs()]


def test_user_record_overrides_seed_with_same_name(tmp_path: Path) -> None:
    svc = LlamaSwapService(
        service_ctx(
            tmp_path,
            options={
                "installables": [
                    {
                        "kind": "github_release",
                        "name": "llama-server-cpu",
                        "label": "custom CPU",
                        "repo": "owner/repo",
                        "asset_pattern": "custom.tar.gz",
                    }
                ]
            },
        )
    )
    record = next(item for item in svc.installable_records() if item.name == "llama-server-cpu")
    assert isinstance(record, GithubReleaseRecord)
    assert record.label == "custom CPU"
    assert record.repo == "owner/repo"
    assert len(svc.installs()) == 5


def test_primary_installable_is_the_llama_swap_proxy(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    primary = svc.primary_installable()
    assert primary is not None
    assert primary.name == "llama-swap"


def test_variant_keys_include_user_record_variant(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path, options={"installables": [_user_record()]}))
    assert svc.variant_keys() == ["cuda", "cpu", "vulkan", "prism"]


def test_set_installable_records_rejection_leaves_records_unchanged(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path, options={"installables": [_user_record()]}))
    before = svc.user_installable_records()
    duplicate_variant = GithubReleaseRecord(
        name="another-fork",
        variant="prism",
        repo="owner/other",
        asset_pattern="*.tar.gz",
    )
    with pytest.raises(ValueError, match="variant keys must be unique"):
        svc.set_installable_records([*before, duplicate_variant])
    assert svc.user_installable_records() == before


def test_set_llama_server_variant_accepts_record_variant(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path, options={"installables": [_user_record()]}))
    svc.set_llama_server_variant("prism")
    assert svc.llama_server_variant == "prism"


def test_set_llama_server_variant_rejects_unknown_variant(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path))
    with pytest.raises(ValueError, match="unknown variant"):
        svc.set_llama_server_variant("metaverse")


def test_backend_of_selected_record_drives_build_options(tmp_path: Path) -> None:
    svc = LlamaSwapService(
        service_ctx(
            tmp_path,
            options={
                "installables": [_user_record()],
                "llama_server_variant": "prism",
            },
        )
    )
    installable = next(item for item in svc.installs() if item.name == "llama-server-prism")
    _install_fake_binary(installable)
    assert svc._build_options().binary_variant == "vulkan"  # noqa: SLF001


def test_disable_installable_uninstalls_and_hides_record(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path, options={"llama_server_variant": None}))
    installable = next(item for item in svc.installs() if item.name == "llama-server-cpu")
    binary = _install_fake_binary(installable)
    svc.disable_installable("llama-server-cpu")
    assert not binary.exists()
    assert "llama-server-cpu" not in [item.name for item in svc.installs()]
    assert (
        next(item for item in svc.installable_records() if item.name == "llama-server-cpu").enabled
        is False
    )


def test_disable_installable_refuses_while_its_binary_is_active(tmp_path: Path) -> None:
    svc = LlamaSwapService(service_ctx(tmp_path, options={"llama_server_variant": "cpu"}))
    installable = next(item for item in svc.installs() if item.name == "llama-server-cpu")
    _install_fake_binary(installable)
    with pytest.raises(RuntimeError, match="active llama-server"):
        svc.disable_installable("llama-server-cpu")


def test_malformed_record_does_not_break_service_construction(tmp_path: Path) -> None:
    svc = LlamaSwapService(
        service_ctx(tmp_path, options={"installables": [{"kind": "unknown", "name": "bad"}]})
    )
    assert len(svc.installs()) == 5
    assert svc.installable_records_error is not None


def test_parse_repo_url_accepts_github_url() -> None:
    assert parse_repo_url("https://github.com/owner/repo") == ("owner", "repo")


def test_parse_repo_url_accepts_releases_suffix() -> None:
    assert parse_repo_url("https://github.com/owner/repo/releases") == ("owner", "repo")
    assert parse_repo_url("https://github.com/owner/repo/releases/latest") == ("owner", "repo")


def test_parse_repo_url_accepts_ssh_form() -> None:
    assert parse_repo_url("git@github.com:owner/repo.git") == ("owner", "repo")


def test_parse_repo_url_rejects_non_github_host() -> None:
    with pytest.raises(ValueError, match="expected a GitHub repository URL"):
        parse_repo_url("https://gitlab.com/owner/repo")


def test_fork_slug_is_lowercase_and_dashed() -> None:
    assert fork_slug("PrismML-Eng", "llama.cpp") == "prismml-eng-llama-cpp"


def test_guess_backend_maps_asset_name_to_backend() -> None:
    assert guess_backend("llama-b1-bin-ubuntu-vulkan-x64.tar.gz") == "vulkan"
    assert guess_backend("llama.cpp-b1-cuda-12.8-amd64.tar.gz") == "cuda"
    assert guess_backend("llama-b1-bin-ubuntu-x64.tar.gz") == "cpu"


def test_candidate_assets_exclude_windows_macos_and_cudart() -> None:
    assets = [
        {"name": "llama-b1-bin-ubuntu-x64.tar.gz", "browser_download_url": "linux"},
        {"name": "llama-b1-bin-win-x64.zip", "browser_download_url": "windows"},
        {"name": "llama-b1-bin-macos-x64.tar.gz", "browser_download_url": "macos"},
        {"name": "cudart-ubuntu-x64.tar.gz", "browser_download_url": "cudart"},
    ]
    assert [asset.name for asset in candidate_assets(assets, "b1")] == [
        "llama-b1-bin-ubuntu-x64.tar.gz"
    ]


@pytest.mark.integration
def test_list_release_candidates_returns_asset_sizes_from_api_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_port()
    server = _FakeServer(port)
    try:
        server.route(
            "/repos/owner/repo/releases/latest",
            json.dumps(
                {
                    "tag_name": "b123",
                    "assets": [
                        {
                            "name": "llama-b123-bin-ubuntu-x64.tar.gz",
                            "browser_download_url": f"http://127.0.0.1:{port}/asset.tar.gz",
                            "size": 12_345,
                        }
                    ],
                }
            ).encode(),
        )
        monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")
        svc = LlamaSwapService(service_ctx(Path("/tmp")))
        candidate = svc.list_release_candidates("https://github.com/owner/repo")
        assert candidate.tag == "b123"
        assert candidate.assets[0].size_bytes == 12_345
    finally:
        server.shutdown()


@pytest.mark.integration
def test_list_release_candidates_rejects_unknown_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_port()
    server = _FakeServer(port)
    try:
        monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")
        svc = LlamaSwapService(service_ctx(Path("/tmp")))
        with pytest.raises(urllib.error.HTTPError):
            svc.list_release_candidates("https://github.com/owner/missing")
    finally:
        server.shutdown()


def test_user_records_survive_service_restart(tmp_path: Path) -> None:
    from genesis_worker.settings import PathsSettings, Settings

    paths = PathsSettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "log",
    )
    settings = Settings(paths=paths)
    sidecar = service_overrides_path(paths.config_dir, "llama_swap")
    write_service_overrides(sidecar, {"installables": [_user_record()]})
    assert settings.options_for("services", "llama_swap")["installables"] == [_user_record()]

    from genesis_worker import GenesisWorker

    worker = GenesisWorker(settings)
    svc = worker.service("llama_swap")
    assert "llama-server-prism" in [item.name for item in svc.installs()]
    assert read_service_overrides(sidecar)["installables"] == [_user_record()]
