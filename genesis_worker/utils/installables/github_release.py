"""Generic GitHub-release installables and release metadata helpers."""

from __future__ import annotations

import fnmatch
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...contracts import (
    AcquireSession,
    InstallState,
    InstallVersion,
    SecretsAccessor,
    ServiceInstall,
)
from ..acquire import GithubReleaseAcquireSession, GithubReleaseTarball
from ..install import InstallLayout, resolve_binary
from .records import GithubReleaseRecord


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class ReleaseCandidate:
    repo: str
    tag: str
    assets: tuple[ReleaseAsset, ...]


def match_asset(asset: dict[str, Any] | str, pattern: str) -> bool:
    """Match an asset name against a glob, ignoring case."""
    name = asset.get("name", "") if isinstance(asset, dict) else asset
    return fnmatch.fnmatchcase(name.lower(), pattern.lower())


def derive_asset_pattern(asset_name: str, tag: str) -> str:
    """Derive a reusable asset glob by replacing the release tag with ``*``."""
    if tag and tag in asset_name:
        return asset_name.replace(tag, "*")
    return f"*{asset_name}"


def find_asset_by_suffix(
    assets: list[dict[str, Any]],
    suffix: str,
) -> dict[str, Any] | None:
    """Return the first asset whose name ends with ``suffix``."""
    suffix_lower = suffix.lower()
    for asset in assets:
        if str(asset.get("name", "")).lower().endswith(suffix_lower):
            return asset
    return None


class GithubReleaseInstall(ServiceInstall):
    """Installable backed by a GitHub release asset selected by a glob."""

    def __init__(
        self,
        record: GithubReleaseRecord,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        secrets: SecretsAccessor | None = None,
    ) -> None:
        self.record = record
        self.name = record.name
        self._layout = InstallLayout(data_dir, state_dir, record.name)
        repo_parts = record.repo.split("/")
        if len(repo_parts) != 2 or not all(repo_parts):
            raise ValueError(f"invalid GitHub repository {record.repo!r}; expected owner/name")
        owner, repo = repo_parts
        checksums_suffix = record.checksums_asset_suffix
        checksums_url = None
        if checksums_suffix is not None:
            checksums_url = lambda release: (
                find_asset_by_suffix(release.get("assets", []), checksums_suffix) or {}
            ).get("browser_download_url")
        self._backend = GithubReleaseTarball(
            name=record.name,
            repo_owner=owner,
            repo_name=repo,
            layout=self._layout,
            cache_root=cache_dir,
            asset_for=lambda assets: next(
                (asset for asset in assets if match_asset(asset, record.asset_pattern)),
                None,
            ),
            binary_rel=record.binary_name,
            checksums_url=checksums_url,
            secrets=secrets,
        )

    def source_url(self) -> str | None:
        return self.record.source_url or f"https://github.com/{self.record.repo}"

    def state(self) -> InstallState:
        return InstallState.INSTALLED if self.binary_path() else InstallState.NOT_INSTALLED

    def installed_version(self) -> str | None:
        return self._layout.resolved_selection()

    def available_versions(self) -> list[InstallVersion]:
        return self._backend.available_versions()

    def invalidate_versions_cache(self) -> None:
        self._backend.invalidate_release_cache()

    def binary_path(self) -> Path | None:
        version = self._layout.resolved_selection()
        if version is None:
            return None
        return resolve_binary(
            self._layout.installs_root / version,
            self.record.binary_name,
        )

    def install(self, *, version: str | None = None) -> AcquireSession:
        return GithubReleaseAcquireSession(backend=self._backend, requested_version=version)

    def uninstall(self, *, version: str | None = None) -> None:
        target = version or self._layout.resolved_selection()
        if target is None:
            return
        target_dir = self._layout.installs_root / target
        if target_dir.exists():
            shutil.rmtree(target_dir)
        symlink = self._layout.current_symlink
        if symlink.is_symlink():
            try:
                if os.readlink(symlink) == target:
                    symlink.unlink()
            except OSError:
                pass


__all__ = [
    "GithubReleaseInstall",
    "ReleaseAsset",
    "ReleaseCandidate",
    "derive_asset_pattern",
    "find_asset_by_suffix",
    "match_asset",
]
