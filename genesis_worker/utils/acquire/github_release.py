"""Acquire session + config holder for GitHub-release-backed installs.

A service that wants to install a binary from GitHub releases:
1. Constructs a :class:`GithubReleaseTarball` with its asset-filter callbacks.
2. Calls :meth:`GithubReleaseTarball.available_versions` for the version picker.
3. Calls :meth:`GithubReleaseTarball.install` to start a session.

The session itself is :class:`GithubReleaseAcquireSession` — a
:class:`BackgroundSession` subclass that streams query → download →
verify → extract. See ADR-028.
"""

from __future__ import annotations

import hashlib
import json
import os
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
    AcquireChoice,
    AcquireProgress,
    AcquireSession,
    AcquireState,
    AcquireStateKind,
    AcquireView,
    InstallVersion,
    SecretsAccessor,
)
from ..background_session import BackgroundSession, _Canceled
from ..install import InstallLayout, Manifest, ManifestSource

_USER_AGENT = "genesis-worker"
_GITHUB_TOKEN_NAME = "github_token"
_DEFAULT_RELEASE_CACHE_TTL_S = 15 * 60  # 15 min — well under the 60/hr unauth limit


# --- pure helpers ----------------------------------------------------------


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


# --- config holder ---------------------------------------------------------


class GithubReleaseTarball:
    """Config holder for a GitHub-release-backed install.

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
        self._auth_token = secrets.get(_GITHUB_TOKEN_NAME) if secrets is not None else None

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

    def _project_to_versions(self, releases: list[dict[str, Any]]) -> list[InstallVersion]:
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

    def install(self, *, version: str | None = None) -> AcquireSession:
        return GithubReleaseAcquireSession(backend=self, requested_version=version)


# --- session ---------------------------------------------------------------


class GithubReleaseAcquireSession(BackgroundSession):
    """Streaming acquire session backed by :class:`GithubReleaseTarball`.

    Drives the install pipeline: query release → download asset → verify
    SHA → extract → write manifest + symlink. Eager: the worker thread
    starts in ``__init__`` since there are no interactive steps before
    the download.
    """

    source_name = "github_release"

    def __init__(
        self,
        *,
        backend: GithubReleaseTarball,
        requested_version: str | None,
    ) -> None:
        backend_name = backend.name
        version_label = requested_version or "latest"
        state = AcquireState(
            kind=AcquireStateKind.FETCHING,
            repo_id=f"{backend_name}@{version_label}",
        )
        super().__init__(state)
        self._backend = backend
        self._requested_version = requested_version
        self._start()

    @property
    def repo_id(self) -> str:
        return self._state.repo_id

    def view(self) -> AcquireView:
        kind = self._state.kind
        repo_id = self._state.repo_id
        if kind == AcquireStateKind.FETCHING:
            tail = self._state.log_tail[-20:]
            return AcquireView(
                kind=kind,
                title=f"Fetching {repo_id}",
                progress=AcquireProgress(
                    bytes_done=self._state.bytes_done,
                    bytes_total=self._state.bytes_total,
                    speed_bps=0,
                    eta_s=0,
                ),
                log_tail=tail,
                can_cancel=True,
            )
        if kind == AcquireStateKind.VERIFYING:
            return AcquireView(
                kind=kind,
                title=f"Verifying {repo_id}",
                can_cancel=True,
            )
        if kind == AcquireStateKind.EXTRACTING:
            return AcquireView(
                kind=kind,
                title=f"Extracting {repo_id}",
                can_cancel=True,
            )
        if kind == AcquireStateKind.COMPLETE:
            return AcquireView(
                kind=kind,
                title=f"Installed {repo_id}",
                can_cancel=False,
            )
        if kind == AcquireStateKind.FAILED:
            tail = self._state.log_tail[-20:]
            return AcquireView(
                kind=kind,
                title=f"Failed: {repo_id}",
                error=self._state.failure,
                log_tail=tail,
                can_cancel=False,
            )
        if kind == AcquireStateKind.CANCELLED:
            return AcquireView(
                kind=kind,
                title="Cancelled",
                can_cancel=False,
            )
        return AcquireView(kind=kind, title=f"Fetching {repo_id}", can_cancel=True)

    def submit(self, choice: AcquireChoice) -> None:
        # Pipelines don't have interactive steps; submit is a no-op.
        return None

    def _run_inner(self) -> None:
        backend = self._backend
        if self._cancel_event.is_set():
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
        if self._cancel_event.is_set():
            raise _Canceled

        asset = backend.asset_for(rel.get("assets", []))
        if asset is None:
            raise RuntimeError(f"no matching asset in release {rel.get('tag_name', '?')}")
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
            self._state.bytes_done = done
            self._state.bytes_total = total or declared_size or 0

        try:
            _http_download(
                asset_url,
                cache_file,
                progress=_progress,
                cancel=self._cancel_event.is_set,
            )
        except (urllib.error.URLError, OSError) as exc:
            if self._cancel_event.is_set():
                raise _Canceled from exc
            raise RuntimeError(f"download failed: {exc}") from exc

        if self._cancel_event.is_set():
            raise _Canceled

        # --- verifying -------------------------------------------------------
        self._state.kind = AcquireStateKind.VERIFYING
        actual_sha = _sha256(cache_file)
        if expected_sha is not None and actual_sha != expected_sha:
            raise RuntimeError(f"sha256 mismatch: expected {expected_sha}, got {actual_sha}")

        # --- extracting ------------------------------------------------------
        install_root = backend.layout.installs_root / version
        if install_root.exists():
            shutil.rmtree(install_root)
        install_root.mkdir(parents=True)
        self._state.kind = AcquireStateKind.EXTRACTING
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

        self._state.kind = AcquireStateKind.COMPLETE


__all__ = ["GithubReleaseAcquireSession", "GithubReleaseTarball"]
