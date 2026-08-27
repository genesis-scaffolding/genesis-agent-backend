"""Installables and the GitHub release tarball backend for llama-swap (ADR-012)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from ...contracts import (
    AcquireProgress,
    AcquireStep,
    InstallSession,
    InstallState,
    InstallVersion,
    SecretsAccessor,
    ServiceInstall,
)
from ...utils.install import (
    BackgroundInstallSession,
    InstallLayout,
    Manifest,
    ManifestSource,
    _Canceled,
)

_USER_AGENT = "genesis-worker"
_GITHUB_TOKEN_NAME = "github_token"
_DEFAULT_RELEASE_CACHE_TTL_S = 15 * 60  # 15 min — well under the 60/hr unauth limit


# --- exceptions -------------------------------------------------------------

# _Canceled is imported from utils.install; it is raised by _http_download
# when the cancel callback fires mid-stream.


# --- pure helpers -----------------------------------------------------------


def _auth_headers_from(token: str | None) -> dict[str, str]:
    """Build an Authorization header for ``token``, or return empty."""
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _http_get_json(
    url: str,
    *,
    timeout: float = 30.0,
    auth_token: str | None = None,
) -> Any:
    """Fetch ``url`` and parse JSON. Returns dict for some endpoints, list for others."""
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    headers.update(_auth_headers_from(auth_token))
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_download(
    url: str,
    dest: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = 600.0,
    auth_token: str | None = None,
) -> None:
    """Stream ``url`` to ``dest``. ``progress(done, total)`` per chunk; total=0 absent."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": _USER_AGENT}
    headers.update(_auth_headers_from(auth_token))
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length", 0)) or 0
            done = 0
            chunk = 64 * 1024
            with dest.open("wb") as f:
                while True:
                    if cancel is not None and cancel():
                        raise _Canceled()
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if progress is not None:
                        progress(done, total)
    except _Canceled:
        dest.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(64 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(dest)
        return
    except (tarfile.ReadError, OSError):
        pass
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def _parse_checksums(content: str, target_filename: str) -> str | None:
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        if name.lstrip("*") == target_filename:
            return digest
    return None


def _resolve_binary(
    install_root: Path, name: str, *, legacy_rel: str | None = None
) -> Path | None:
    """Locate a binary file inside the extracted install tree.

    Order:
      1. The legacy ``legacy_rel`` path (matches llama-swap, where the
         binary sits at the archive root).
      2. A recursive search by filename (matches ai-dock's llama.cpp-cuda
         release, where the binary lives at ``cuda-12.8/llama-server``).

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


# --- backend ---------------------------------------------------------------


class GithubReleaseTarball:
    """Backend that fetches one asset per upstream release.

    ``asset_for(assets)`` selects the asset to download. ``checksums_url``
    may return a URL whose body maps ``<filename> → sha256``; if it returns
    None, verification is skipped and the manifest records ``verified: false``.
    """

    API_BASE_ENV: ClassVar[str] = "GENESIS_INSTALL_GITHUB_API"
    DEFAULT_API_BASE: ClassVar[str] = "https://api.github.com"
    DEFAULT_MAX_RELEASES: ClassVar[int] = 50

    def __init__(
        self,
        *,
        name: str,
        repo_owner: str,
        repo_name: str,
        layout: InstallLayout,
        cache_root: Path,
        asset_for: Callable[[list[dict[str, Any]]], dict[str, Any] | None],
        binary_rel: str,
        checksums_url: Callable[[dict[str, Any]], str | None] | None = None,
        install_method: str = "github_release_tarball",
        max_releases: int = DEFAULT_MAX_RELEASES,
        release_cache_ttl_s: int = _DEFAULT_RELEASE_CACHE_TTL_S,
        secrets: SecretsAccessor | None = None,
    ) -> None:
        self.name = name
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.layout = layout
        self.cache_root = cache_root
        self.asset_for = asset_for
        self.binary_rel = binary_rel
        self.checksums_url = checksums_url
        self.install_method = install_method
        self.max_releases = max_releases
        self.release_cache_ttl_s = release_cache_ttl_s
        self._auth_token = (
            secrets.get(_GITHUB_TOKEN_NAME) if secrets is not None else None
        )

    def _release_cache_path(self) -> Path:
        safe = f"{self.repo_owner}__{self.repo_name}".replace("/", "_")
        return self.cache_root / "releases-cache" / f"{safe}.json"

    def invalidate_release_cache(self) -> None:
        """Drop the on-disk release cache; the next ``available_versions()`` refetches."""
        p = self._release_cache_path()
        if p.exists():
            p.unlink()

    def _read_release_cache(self) -> list[dict[str, Any]] | None:
        p = self._release_cache_path()
        if not p.is_file():
            return None
        try:
            with p.open() as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("version") != 1:
            return None
        fetched_at = data.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return None
        if (time.time() - fetched_at) >= self.release_cache_ttl_s:
            return None
        releases = data.get("releases")
        if not isinstance(releases, list):
            return None
        return releases

    def _write_release_cache(self, releases: list[dict[str, Any]]) -> None:
        p = self._release_cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        with tmp.open("w") as f:
            json.dump({"version": 1, "fetched_at": time.time(), "releases": releases}, f)
        os.replace(tmp, p)

    @classmethod
    def api_base(cls) -> str:
        return os.environ.get(cls.API_BASE_ENV, cls.DEFAULT_API_BASE)

    def release_url(self) -> str:
        return (
            f"{self.api_base().rstrip('/')}"
            f"/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
        )

    def releases_url(self) -> str:
        return (
            f"{self.api_base().rstrip('/')}"
            f"/repos/{self.repo_owner}/{self.repo_name}/releases"
            f"?per_page={self.max_releases}"
        )

    def tag_url(self, version: str) -> str:
        return (
            f"{self.api_base().rstrip('/')}"
            f"/repos/{self.repo_owner}/{self.repo_name}/releases/tags/{version}"
        )

    def available_versions(self) -> list[InstallVersion]:
        """All releases with a matching asset, newest first.

        SHA is not fetched here; it is resolved at install time so the
        version picker is one network call instead of N (the unauthenticated
        GitHub API is 60 req/hr).

        Cached on disk for ``release_cache_ttl_s`` (default 15 min). Call
        :meth:`invalidate_release_cache` to force a refetch.
        """
        cached = self._read_release_cache()
        if cached is not None:
            return self._project_to_versions(cached)

        releases = _http_get_json(self.releases_url(), auth_token=self._auth_token)
        if not isinstance(releases, list):
            return []
        self._write_release_cache(releases)
        return self._project_to_versions(releases)

    def _project_to_versions(
        self, releases: list[dict[str, Any]]
    ) -> list[InstallVersion]:
        out: list[InstallVersion] = []
        for rel in releases:
            asset = self.asset_for(rel.get("assets", []))
            if asset is None:
                continue
            out.append(
                InstallVersion(
                    version=rel.get("tag_name", ""),
                    url=asset.get("browser_download_url", ""),
                    sha256=None,
                    size_bytes=asset.get("size"),
                )
            )
        return out

    def install(self, *, version: str | None = None) -> InstallSession:
        return _GithubReleaseInstallSession(backend=self, requested_version=version)


# --- session ---------------------------------------------------------------


class _GithubReleaseInstallSession(BackgroundInstallSession):
    """Streaming install session backed by ``GithubReleaseTarball``."""

    def __init__(
        self,
        *,
        backend: GithubReleaseTarball,
        requested_version: str | None,
    ) -> None:
        self._backend = backend
        self._requested_version = requested_version
        super().__init__()

    @property
    def _name(self) -> str:
        return self._backend.name

    def _run_inner(self) -> None:
        backend = self._backend
        self._publish(
            AcquireStep(kind="fetching", title=f"querying {backend.name} releases")
        )
        if self._cancel.is_set():
            raise _Canceled
        if self._requested_version:
            rel = _http_get_json(
                backend.tag_url(self._requested_version),
                auth_token=backend._auth_token,
            )
        else:
            rel = _http_get_json(
                backend.release_url(),
                auth_token=backend._auth_token,
            )
        if self._cancel.is_set():
            raise _Canceled

        asset = backend.asset_for(rel.get("assets", []))
        if asset is None:
            raise RuntimeError(
                f"no matching asset in release {rel.get('tag_name', '?')}"
            )
        version = rel.get("tag_name", "")
        asset_name = asset.get("name", "")
        asset_url = asset.get("browser_download_url", "")
        if not version or not asset_name or not asset_url:
            raise RuntimeError(f"incomplete release record: {rel!r}")

        expected_sha: str | None = None
        if backend.checksums_url is not None:
            checksums_url = backend.checksums_url(rel)
            if checksums_url is not None:
                cs_headers = {"User-Agent": _USER_AGENT}
                cs_headers.update(_auth_headers_from(backend._auth_token))
                req = urllib.request.Request(checksums_url, headers=cs_headers)
                with urllib.request.urlopen(req, timeout=30.0) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                expected_sha = _parse_checksums(text, asset_name)

        cache_dir = backend.cache_root / backend.name / version
        cache_file = cache_dir / asset_name
        declared_size = asset.get("size") or 0

        # --- fetching --------------------------------------------------------
        last_t = time.monotonic()
        last_done = 0
        speed_bps = 0

        def _progress(done: int, total: int) -> None:
            nonlocal last_t, last_done, speed_bps
            now = time.monotonic()
            if now - last_t >= 0.5:
                speed_bps = int((done - last_done) / (now - last_t))
                last_done = done
                last_t = now
            eta = int((total - done) / speed_bps) if speed_bps and total else 0
            self._publish(
                AcquireStep(
                    kind="fetching",
                    title=f"downloading {asset_name}",
                    total_bytes=total or declared_size or None,
                    progress=AcquireProgress(
                        bytes_done=done,
                        bytes_total=total or declared_size or 0,
                        speed_bps=speed_bps,
                        eta_s=eta,
                    ),
                    cache_dir=cache_dir,
                )
            )

        self._publish(
            AcquireStep(
                kind="fetching",
                title=f"downloading {asset_name}",
                total_bytes=declared_size or None,
                cache_dir=cache_dir,
            )
        )
        try:
            _http_download(
                asset_url,
                cache_file,
                progress=_progress,
                cancel=self._cancel.is_set,
            )
        except (urllib.error.URLError, OSError) as exc:
            if self._cancel.is_set():
                raise _Canceled from exc
            raise RuntimeError(f"download failed: {exc}") from exc

        if self._cancel.is_set():
            raise _Canceled

        # --- verifying -------------------------------------------------------
        self._publish(
            AcquireStep(
                kind="verifying",
                title=f"verifying {asset_name}",
                cache_dir=cache_dir,
            )
        )
        actual_sha = _sha256(cache_file)
        if expected_sha is not None and actual_sha != expected_sha:
            raise RuntimeError(
                f"sha256 mismatch: expected {expected_sha}, got {actual_sha}"
            )

        # --- extracting ------------------------------------------------------
        install_root = backend.layout.installs_root / version
        if install_root.exists():
            shutil.rmtree(install_root)
        install_root.mkdir(parents=True)
        self._publish(
            AcquireStep(
                kind="extracting",
                title=f"extracting {asset_name}",
                cache_dir=install_root,
            )
        )
        try:
            _extract(cache_file, install_root)
        except Exception as exc:
            shutil.rmtree(install_root, ignore_errors=True)
            raise RuntimeError(f"extract failed: {exc}") from exc

        # --- manifest + symlink ---------------------------------------------
        manifest = Manifest(
            name=backend.name,
            version=version,
            source=ManifestSource(url=asset_url),
            sha256=expected_sha,
            verified=expected_sha is not None,
            fetched_at=datetime.now(UTC).isoformat(),
            size_bytes=declared_size or cache_file.stat().st_size,
            install_method=backend.install_method,
        )
        manifest.to_yaml(backend.layout.manifest_path(version))
        backend.layout.set_current_symlink(version)

        self._publish(
            AcquireStep(
                kind="complete",
                title=f"installed {version}",
                cache_dir=install_root,
            )
        )


# --- installables ----------------------------------------------------------


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


def _find_asset_by_suffix(assets: list[dict[str, Any]], suffix: str) -> dict[str, Any] | None:
    """Return the first asset whose name ends with ``suffix`` (case-insensitive)."""
    suffix_lower = suffix.lower()
    for a in assets:
        if a.get("name", "").lower().endswith(suffix_lower):
            return a
    return None


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
        secrets: SecretsAccessor | None = None,
    ) -> None:
        self._layout = InstallLayout(data_dir, state_dir, "llama-swap")
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

    def install(self, *, version: str | None = None) -> InstallSession:
        return self._backend.install(version=version)

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
    """Installable for the ai-dock/llama.cpp-cuda release (CUDA-enabled llama-server).

    The CUDA build is fetched from ai-dock's third-party repo because it bundles
    CUDA libs (no separate toolkit needed). The upstream llama.cpp CUDA build
    requires a CUDA toolkit install, so this is a more pragmatic choice.

    .. note::
        Not wired into the running llama-swap in v1 — the recipe still
        names ``vendor/llama.cpp/build/bin/llama-server``. Integration
        lands when recipes migrate.
    """

    name = "llama-server-cuda"
    binary_name = "llama-server"

    def __init__(
        self,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        secrets: SecretsAccessor | None = None,
    ) -> None:
        self._layout = InstallLayout(data_dir, state_dir, self.name)
        self._backend = GithubReleaseTarball(
            name=self.name,
            repo_owner="ai-dock",
            repo_name="llama.cpp-cuda",
            layout=self._layout,
            cache_root=cache_dir,
            asset_for=lambda assets: next(
                (a for a in assets if _ai_dock_llama_cpp_cuda_asset(a)),
                None,
            ),
            binary_rel=self.binary_name,
            secrets=secrets,
        )

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

    def install(self, *, version: str | None = None) -> InstallSession:
        return self._backend.install(version=version)

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
        secrets: SecretsAccessor | None = None,
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

    def install(self, *, version: str | None = None) -> InstallSession:
        return self._backend.install(version=version)

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
        secrets: SecretsAccessor | None = None,
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
        secrets: SecretsAccessor | None = None,
    ) -> None:
        super().__init__(
            data_dir=data_dir,
            cache_dir=cache_dir,
            state_dir=state_dir,
            asset_matcher=_upstream_llama_vulkan_asset,
            secrets=secrets,
        )


__all__ = [
    "GithubReleaseTarball",
    "LlamaServerCPU",
    "LlamaServerCUDA",
    "LlamaServerVulkan",
    "LlamaSwapBinary",
]
