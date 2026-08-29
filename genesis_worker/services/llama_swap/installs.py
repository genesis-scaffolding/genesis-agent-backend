"""Installables for llama-swap and the upstream llama.cpp builds (ADR-012, ADR-028).

Asset filters (which file to download) and the ``ServiceInstall``
subclasses live here. The GitHub release runtime
(:class:`~genesis_worker.utils.acquire.github_release.GithubReleaseTarball`
and ``GithubReleaseAcquireSession``) is in ``utils/acquire/`` and
reused across services.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...contracts import (
    AcquireSession,
    InstallState,
    InstallVersion,
    ServiceInstall,
)
from ...utils.acquire import GithubReleaseAcquireSession, GithubReleaseTarball
from ...utils.install import InstallLayout

_GITHUB_TOKEN_NAME = "github_token"


def _find_asset_by_suffix(assets: list[dict[str, Any]], suffix: str) -> dict[str, Any] | None:
    """Return the first asset whose name ends with ``suffix`` (case-insensitive)."""
    suffix_lower = suffix.lower()
    for a in assets:
        if a.get("name", "").lower().endswith(suffix_lower):
            return a
    return None


def _resolve_binary(
    install_root: Path, name: str, *, legacy_rel: str | None = None
) -> Path | None:
    """Locate a binary file inside the extracted install tree.

    Order:
      1. The legacy ``legacy_rel`` path (matches llama-swap, where the
         binary sits at the archive root).
      2. A recursive search by filename (matches genesis-scaffolding's
         llama.cpp-cuda release, where the binary lives at ``cuda-12.8/llama-server``).

    Returns the first matching file under ``install_root``, or ``None``
    if no candidate exists.
    """
    if legacy_rel:
        legacy = install_root / legacy_rel
        if legacy.is_file():
            return legacy
    if not name:
        return None
    for candidate in install_root.rglob(name):
        if candidate.is_file():
            return candidate
    return None


# --- asset filters ---------------------------------------------------------


_LINUX_AMD64_TARBALL_RE = re.compile(r"_linux_(?:amd64|x86_64)\.tar\.gz$", re.IGNORECASE)


def _asset_name_matches_linux_amd64_tarball(asset: dict[str, Any]) -> bool:
    """Match ``..._linux_amd64.tar.gz`` at the end of an asset name.

    GoReleaser-tagged releases use ``amd64`` for x86_64 Linux; we also
    accept ``x86_64`` so the matcher doesn't break if upstream renames
    its convention.
    """
    return bool(_LINUX_AMD64_TARBALL_RE.search(asset.get("name", "")))


_LLAMA_CPP_CUDA_AMD64_TARBALL_RE = re.compile(
    r"^llama\.cpp-(?:b\d+|v\d+\.\d+\.\d+)-cuda-\d+\.\d+-amd64\.tar\.gz$",
    re.IGNORECASE,
)


def _ai_dock_llama_cpp_cuda_asset(asset: dict[str, Any]) -> bool:
    """Match ``llama.cpp-(b<build>|v<semver>)-cuda-<ver>-amd64.tar.gz`` (ai-dock naming)."""
    return bool(_LLAMA_CPP_CUDA_AMD64_TARBALL_RE.match(asset.get("name", "")))


_UPSTREAM_LLAMA_CPU_TARBALL_RE = re.compile(
    r"^llama-.+-bin-ubuntu-x64\.tar\.gz$", re.IGNORECASE
)
_UPSTREAM_LLAMA_VULKAN_TARBALL_RE = re.compile(
    r"^llama-.+-bin-ubuntu-vulkan-x64\.tar\.gz$", re.IGNORECASE
)


def _upstream_llama_cpu_asset(asset: dict[str, Any]) -> bool:
    """Match upstream llama.cpp CPU build: ``llama-<v>-bin-ubuntu-x64.tar.gz``."""
    return bool(_UPSTREAM_LLAMA_CPU_TARBALL_RE.match(asset.get("name", "")))


def _upstream_llama_vulkan_asset(asset: dict[str, Any]) -> bool:
    """Match upstream llama.cpp Vulkan build: ``llama-<v>-bin-ubuntu-vulkan-x64.tar.gz``."""
    return bool(_UPSTREAM_LLAMA_VULKAN_TARBALL_RE.match(asset.get("name", "")))


# --- installables ----------------------------------------------------------


class LlamaSwapBinary(ServiceInstall):
    """Installable for the llama-swap executable (mostlygeek/llama-swap releases)."""

    name = "llama-swap"
    binary_name = "llama-swap"

    def __init__(
        self,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        secrets=None,
    ) -> None:
        self._layout = InstallLayout(data_dir, state_dir, "llama-swap")
        self._secrets = secrets
        self._backend = GithubReleaseTarball(
            name="llama-swap",
            repo_owner="mostlygeek",
            repo_name="llama-swap",
            layout=self._layout,
            cache_root=cache_dir,
            asset_for=lambda assets: next(
                (a for a in assets if _asset_name_matches_linux_amd64_tarball(a)),
                None,
            ),
            binary_rel="llama-swap",
            checksums_url=lambda rel: (
                _find_asset_by_suffix(rel.get("assets", []), "_checksums.txt")
                or {}
            ).get("browser_download_url"),
            secrets=secrets,
        )

    def source_url(self) -> str | None:
        return "https://github.com/mostlygeek/llama-swap"

    def state(self) -> InstallState:
        return InstallState.INSTALLED if self.binary_path() else InstallState.NOT_INSTALLED

    def installed_version(self) -> str | None:
        return self._layout.resolved_selection()

    def available_versions(self) -> list[InstallVersion]:
        return self._backend.available_versions()

    def invalidate_versions_cache(self) -> None:
        """Force the next available_versions() call to refetch from upstream."""
        self._backend.invalidate_release_cache()

    def binary_path(self) -> Path | None:
        version = self._layout.resolved_selection()
        if version is None:
            return None
        return _resolve_binary(
            self._layout.installs_root / version,
            self.binary_name,
            legacy_rel=self._backend.binary_rel,
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
        sym = self._layout.current_symlink
        if sym.is_symlink():
            try:
                if os.readlink(sym) == target:
                    sym.unlink()
            except OSError:
                pass


class LlamaServerCUDA(ServiceInstall):
    """Installable for the genesis-scaffolding/llama.cpp-cuda release (CUDA-enabled llama-server).

    The CUDA build is fetched from genesis-scaffolding's fork because it bundles
    CUDA libs (no separate toolkit needed). The upstream llama.cpp CUDA build
    requires a CUDA toolkit install, so this is a more pragmatic choice.

    .. note::
        Not wired into the running llama-swap in v1 — the recipe still
        names ``vendor/llama.cpp/build/bin/llama-server``. Integration
        lands when recipes migrate.
    """

    name = "llama-server-cuda"
    binary_name = "llama-server"
    repo_owner = "genesis-scaffolding"
    repo_name = "llama.cpp-cuda"

    def __init__(
        self,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        secrets=None,
    ) -> None:
        self._layout = InstallLayout(data_dir, state_dir, self.name)
        self._backend = GithubReleaseTarball(
            name=self.name,
            repo_owner=self.repo_owner,
            repo_name=self.repo_name,
            layout=self._layout,
            cache_root=cache_dir,
            asset_for=lambda assets: next(
                (a for a in assets if _ai_dock_llama_cpp_cuda_asset(a)),
                None,
            ),
            binary_rel=self.binary_name,
            secrets=secrets,
        )

    def source_url(self) -> str | None:
        return f"https://github.com/{self.repo_owner}/{self.repo_name}"

    def state(self) -> InstallState:
        return InstallState.INSTALLED if self.binary_path() else InstallState.NOT_INSTALLED

    def installed_version(self) -> str | None:
        return self._layout.resolved_selection()

    def available_versions(self) -> list[InstallVersion]:
        return self._backend.available_versions()

    def invalidate_versions_cache(self) -> None:
        """Force the next available_versions() call to refetch from upstream."""
        self._backend.invalidate_release_cache()

    def binary_path(self) -> Path | None:
        version = self._layout.resolved_selection()
        if version is None:
            return None
        return _resolve_binary(
            self._layout.installs_root / version,
            self.binary_name,
            legacy_rel=self._backend.binary_rel,
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
        sym = self._layout.current_symlink
        if sym.is_symlink():
            try:
                if os.readlink(sym) == target:
                    sym.unlink()
            except OSError:
                pass


class _UpstreamLlamaServerBinary(ServiceInstall):
    """Base for upstream ``ggml-org/llama.cpp`` variants (CPU, Vulkan, ...).

    Subclasses set ``name`` and pass an ``asset_matcher`` to ``super().__init__``.
    The shared ``ggml-org/llama.cpp`` repo and 15-min release cache mean
    the upstream CPU and Vulkan installables can share one release list
    under the hood — only the asset filter differs.
    """

    binary_name = "llama-server"
    repo_owner = "ggml-org"
    repo_name = "llama.cpp"
    _asset_matcher: Callable[[dict[str, Any]], bool]
    _backend: GithubReleaseTarball

    def __init__(
        self,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        asset_matcher: Callable[[dict[str, Any]], bool],
        secrets=None,
    ) -> None:
        self._asset_matcher = asset_matcher
        self._layout = InstallLayout(data_dir, state_dir, self.name)
        self._backend = GithubReleaseTarball(
            name=self.name,
            repo_owner=self.repo_owner,
            repo_name=self.repo_name,
            layout=self._layout,
            cache_root=cache_dir,
            asset_for=lambda assets: next(
                (a for a in assets if asset_matcher(a)),
                None,
            ),
            binary_rel=self.binary_name,
            secrets=secrets,
        )

    def source_url(self) -> str | None:
        return f"https://github.com/{self.repo_owner}/{self.repo_name}"

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
        return _resolve_binary(
            self._layout.installs_root / version,
            self.binary_name,
            legacy_rel=self._backend.binary_rel,
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
        sym = self._layout.current_symlink
        if sym.is_symlink():
            try:
                if os.readlink(sym) == target:
                    sym.unlink()
            except OSError:
                pass


class LlamaServerCPU(_UpstreamLlamaServerBinary):
    """Installable for the upstream llama.cpp CPU build (``bin-ubuntu-x64.tar.gz``)."""

    name = "llama-server-cpu"

    def __init__(
        self,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        secrets=None,
    ) -> None:
        super().__init__(
            data_dir=data_dir,
            cache_dir=cache_dir,
            state_dir=state_dir,
            asset_matcher=_upstream_llama_cpu_asset,
            secrets=secrets,
        )


class LlamaServerVulkan(_UpstreamLlamaServerBinary):
    """Installable for the upstream llama.cpp Vulkan build (``bin-ubuntu-vulkan-x64.tar.gz``)."""

    name = "llama-server-vulkan"

    def __init__(
        self,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        secrets=None,
    ) -> None:
        super().__init__(
            data_dir=data_dir,
            cache_dir=cache_dir,
            state_dir=state_dir,
            asset_matcher=_upstream_llama_vulkan_asset,
            secrets=secrets,
        )


__all__ = [
    "LlamaServerCPU",
    "LlamaServerCUDA",
    "LlamaServerVulkan",
    "LlamaSwapBinary",
]
