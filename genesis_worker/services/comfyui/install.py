"""Installable for the ComfyUI Docker image.

The installable owns version listing, arch filtering, the selection
file, and uninstall. The actual pull is driven by
:class:`~genesis_worker.utils.acquire.docker_pull.DockerPullAcquireSession`,
which streams ``docker pull --progress=json`` stderr lines as
``AcquireView`` updates.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import subprocess
import time
from pathlib import Path

from ...contracts import (
    AcquireSession,
    InstallState,
    InstallVersion,
    ServiceInstall,
)
from ...utils.acquire import DockerPullAcquireSession
from ...utils.process import DockerContainer

_RELEASE_CACHE_TTL_S = 15 * 60  # 15 min — same as GithubReleaseTarball.

# Tag suffixes for the architectures we care about. The cosy convention
# here is that any tag containing one of these segments is arch-pinned;
# tags without an arch segment (``latest``, ``v0.34.0``, ...) are
# arch-agnostic and pass through any filter.
_ARCH_SUFFIXES = ("amd64", "arm64")
_TAG_ARCH_RE = re.compile(r"-(?P<arch>amd64|arm64)(?=$|[-+])", re.IGNORECASE)


def _normalise_host_arch(raw: str | None) -> str | None:
    """Map ``platform.machine()`` output to a tag suffix, or pass through.

    Returns one of ``"amd64"``, ``"arm64"``, ``""`` (no filter), or
    ``None`` (auto-detect on first ``available_versions`` call).
    """
    if raw is None:
        return None
    if raw == "":
        return ""
    machine = raw.lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    # Unknown arch — don't filter; show all tags.
    return ""


def detect_host_arch() -> str:
    """Best-effort host-arch detection via ``platform.machine()``."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return ""


def _matches_host_arch(tag: str, host_arch: str) -> bool:
    """True if ``tag`` should be shown to a user on ``host_arch``.

    Tags with no arch segment (e.g. ``v0.34.0``) match any host. Tags
    with an arch segment only match if it equals the host arch.
    """
    m = _TAG_ARCH_RE.search(tag.lower())
    if m is None:
        return True
    return m.group("arch").lower() == host_arch.lower()


def _cache_path(cache_root: Path, repo: str) -> Path:
    safe = repo.replace("/", "_")
    return cache_root / "releases-cache" / f"{safe}.json"


def _read_cache(path: Path, ttl_s: int) -> list[str] | None:
    if not path.is_file():
        return None
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    fetched_at = data.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    if (time.time() - fetched_at) >= ttl_s:
        return None
    tags = data.get("tags")
    if not isinstance(tags, list):
        return None
    return [t for t in tags if isinstance(t, str)]


def _write_cache(path: Path, tags: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    payload = {"version": 1, "fetched_at": time.time(), "tags": tags}
    with tmp.open("w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


class ComfyUiImage(ServiceInstall):
    """Installable for the ComfyUI Docker image.

    ``name`` mirrors the upstream image so the Binaries-style UI page
    reads naturally. ``binary_path()`` returns ``None`` — there is no
    host binary for a container service; ``is_available()`` consults
    ``state()`` directly.
    """

    name = "comfyui-cuda"

    def __init__(
        self,
        *,
        data_dir: Path,
        cache_dir: Path,
        state_dir: Path,
        image_repo: str,
        image_tag: str,
        host_arch: str | None = None,
        secrets=None,  # accepted but unused for v1 (public GHCR)
    ) -> None:
        self._image_repo = image_repo
        self._image_tag = image_tag
        self._host_arch = _normalise_host_arch(host_arch)
        self._cache_dir = cache_dir
        self._state_dir = state_dir
        self._selection_path = state_dir / "current"

    @property
    def image_ref(self) -> str:
        return f"{self._image_repo}:{self._image_tag}"

    def source_url(self) -> str | None:
        return "https://github.com/genesis-scaffolding/comfyui-cuda/pkgs/container/comfyui-cuda"

    def state(self) -> InstallState:
        return (
            InstallState.INSTALLED
            if DockerContainer.image_present(self.image_ref)
            else InstallState.NOT_INSTALLED
        )

    def installed_version(self) -> str | None:
        return self._selection_path.read_text().strip() if self._selection_path.is_file() else None

    def available_versions(self) -> list[InstallVersion]:
        """All tags reachable from the registry, filtered by host arch.

        Newest-first is the registry's order. 15-min on-disk cache
        mirrors ``GithubReleaseTarball.available_versions``. Tags with
        an explicit arch suffix (``-amd64`` / ``-arm64``) that doesn't
        match the host are filtered out; arch-agnostic tags (e.g.
        ``v0.34.0``) pass through.
        """
        cache = _cache_path(self._cache_dir, self._image_repo)
        cached = _read_cache(cache, _RELEASE_CACHE_TTL_S)
        if cached is not None:
            tags = cached
        else:
            tags = DockerContainer.list_remote_tags(self._image_repo)
            _write_cache(cache, tags)

        host_arch = self._host_arch if self._host_arch is not None else detect_host_arch()
        if host_arch:
            tags = [t for t in tags if _matches_host_arch(t, host_arch)]
        return self._project_to_versions(tags)

    def invalidate_versions_cache(self) -> None:
        """Force the next ``available_versions()`` call to refetch."""
        cache = _cache_path(self._cache_dir, self._image_repo)
        if cache.exists():
            cache.unlink()

    def _project_to_versions(self, tags: list[str]) -> list[InstallVersion]:
        return [
            InstallVersion(
                version=tag,
                url=f"{self._image_repo}:{tag}",
                sha256=None,
                size_bytes=None,
            )
            for tag in tags
        ]

    def binary_path(self) -> Path | None:
        return None

    def install(self, *, version: str | None = None) -> AcquireSession:
        target_tag = version or self._image_tag
        return DockerPullAcquireSession(
            image=f"{self._image_repo}:{target_tag}",
            on_complete=lambda: self._record_selection(target_tag),
        )

    def _record_selection(self, tag: str) -> None:
        """Write the installed tag to the selection file on successful install."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._selection_path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        with tmp.open("w") as f:
            f.write(tag)
        os.replace(tmp, self._selection_path)

    def uninstall(self, *, version: str | None = None) -> None:
        target = version or self.installed_version()
        if target is None:
            return
        # ``docker rmi`` returns non-zero when the image is in use; the
        # service's ``uninstall_installable`` guard prevents that case.
        subprocess.run(
            ["docker", "rmi", f"{self._image_repo}:{target}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
        # Selection is removed only when the user uninstalls the currently
        # selected tag.
        if target == self.installed_version() and self._selection_path.is_file():
            self._selection_path.unlink()


__all__ = ["ComfyUiImage"]
