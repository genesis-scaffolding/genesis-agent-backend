"""Build service installables from declarative records."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from ...contracts import SecretsAccessor, ServiceInstall
from .docker_image import DockerImageInstall
from .github_release import GithubReleaseInstall
from .records import DockerImageRecord, GithubReleaseRecord, InstallableRecord


@dataclass(frozen=True)
class InstallDirs:
    data_dir: Path
    cache_dir: Path
    state_dir: Path


def build_installable(
    record: InstallableRecord,
    dirs: InstallDirs,
    *,
    secrets: SecretsAccessor | None = None,
) -> ServiceInstall:
    """Build the installable implementation named by ``record.kind``."""
    if isinstance(record, GithubReleaseRecord):
        return GithubReleaseInstall(
            record,
            data_dir=dirs.data_dir,
            cache_dir=dirs.cache_dir,
            state_dir=dirs.state_dir,
            secrets=secrets,
        )
    if isinstance(record, DockerImageRecord):
        tag_filter = None
        if record.tag_pattern:
            pattern = record.tag_pattern.lower()
            tag_filter = lambda tag: fnmatch.fnmatchcase(tag.lower(), pattern)
        return DockerImageInstall(
            image_repo=record.image,
            image_tag=record.default_tag,
            cache_dir=dirs.cache_dir,
            state_dir=dirs.state_dir / record.name,
            name=record.name,
            source_url=None,
            arch_filter=tag_filter,
            secrets=secrets,
        )
    raise ValueError(f"unsupported installable kind: {record.kind!r}")


__all__ = ["InstallDirs", "build_installable"]
