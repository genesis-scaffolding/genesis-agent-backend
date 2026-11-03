"""Installable backed by a registry image and its version tags."""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import Callable
from pathlib import Path

from ...contracts import (
    AcquireSession,
    InstallState,
    InstallVersion,
    SecretsAccessor,
    ServiceInstall,
)
from ..acquire import DockerPullAcquireSession
from ..process import DockerContainer
from ..registry_tags import list_image_tags, read_tag_cache, tag_cache_path, write_tag_cache

_RELEASE_CACHE_TTL_S = 15 * 60


class DockerImageInstall(ServiceInstall):
    """One installable Docker image with tag discovery and pull progress."""

    def __init__(
        self,
        *,
        image_repo: str,
        image_tag: str,
        cache_dir: Path,
        state_dir: Path,
        name: str,
        source_url: str | None,
        arch_filter: Callable[[str], bool] | None = None,
        secrets: SecretsAccessor | None = None,
    ) -> None:
        self._image_repo = image_repo
        self._image_tag = image_tag
        self._cache_dir = cache_dir
        self._state_dir = state_dir
        self._selection_path = state_dir / "current"
        self._source_url = source_url
        self._arch_filter = arch_filter
        self._auth_token = secrets.get("github_token") if secrets is not None else None
        self.name = name

    @property
    def image_ref(self) -> str:
        return f"{self._image_repo}:{self._image_tag}"

    def source_url(self) -> str | None:
        return self._source_url

    def state(self) -> InstallState:
        tag = self.installed_version() or self._image_tag
        return (
            InstallState.INSTALLED
            if DockerContainer.image_present(f"{self._image_repo}:{tag}")
            else InstallState.NOT_INSTALLED
        )

    def installed_version(self) -> str | None:
        return self._selection_path.read_text().strip() if self._selection_path.is_file() else None

    def available_versions(self) -> list[InstallVersion]:
        cache = tag_cache_path(self._cache_dir, self._image_repo)
        cached = read_tag_cache(cache, _RELEASE_CACHE_TTL_S)
        if cached is not None:
            tags = cached
        else:
            tags = list_image_tags(self._image_repo, auth_token=self._auth_token)
            if tags:
                write_tag_cache(cache, tags)
            else:
                tags = read_tag_cache(cache, None) or []
        if self._arch_filter is not None:
            tags = [tag for tag in tags if self._arch_filter(tag)]
        return [
            InstallVersion(
                version=tag,
                url=f"{self._image_repo}:{tag}",
                sha256=None,
                size_bytes=None,
            )
            for tag in tags
        ]

    def invalidate_versions_cache(self) -> None:
        cache = tag_cache_path(self._cache_dir, self._image_repo)
        if cache.exists():
            cache.unlink()

    def binary_path(self) -> Path | None:
        return None

    def install(self, *, version: str | None = None) -> AcquireSession:
        target_tag = version or self._image_tag
        return DockerPullAcquireSession(
            image=f"{self._image_repo}:{target_tag}",
            on_complete=lambda: self._record_selection(target_tag),
        )

    def _record_selection(self, tag: str) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._selection_path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        with tmp.open("w") as f:
            f.write(tag)
        os.replace(tmp, self._selection_path)

    def uninstall(self, *, version: str | None = None) -> None:
        target = version or self.installed_version()
        if target is None:
            return
        subprocess.run(
            ["docker", "rmi", f"{self._image_repo}:{target}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
        if target == self.installed_version() and self._selection_path.is_file():
            self._selection_path.unlink()


__all__ = ["DockerImageInstall"]
