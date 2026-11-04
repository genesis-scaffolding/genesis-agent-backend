"""Declarative installable records and their implementations."""

from .docker_image import DockerImageInstall
from .factory import InstallDirs, build_installable
from .github_release import (
    GithubReleaseInstall,
    ReleaseAsset,
    ReleaseCandidate,
    derive_asset_pattern,
    find_asset_by_suffix,
    match_asset,
)
from .records import (
    DockerImageRecord,
    GithubReleaseRecord,
    InstallableBase,
    InstallableRecord,
    InstallableRecordAdapter,
    merge_records,
    parse_installable_record,
)

__all__ = [
    "DockerImageInstall",
    "DockerImageRecord",
    "GithubReleaseInstall",
    "GithubReleaseRecord",
    "InstallDirs",
    "InstallableBase",
    "InstallableRecord",
    "InstallableRecordAdapter",
    "ReleaseAsset",
    "ReleaseCandidate",
    "build_installable",
    "derive_asset_pattern",
    "find_asset_by_suffix",
    "match_asset",
    "merge_records",
    "parse_installable_record",
]
