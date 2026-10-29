"""Remote image tag listing for registry-backed images.

The registry is the source of truth. GHCR's anonymous token endpoint
throttles by IP and stalls the connection instead of returning 429, so a
single failed token fetch loses the whole listing. When the registry
yields nothing, fall back to the GitHub releases of the matching repo:
our image repos publish the tags they pushed in their release bodies.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

from .process import DockerContainer

_GITHUB_API = "https://api.github.com"
_USER_AGENT = "genesis-worker"
_TIMEOUT_S = 15.0
_RETRY_DELAYS_S = (0.0, 1.5, 3.0)
_TAG_PATTERN = r"([A-Za-z0-9][A-Za-z0-9._+-]*)"


def list_image_tags(repo: str, *, auth_token: str | None = None) -> list[str]:
    """Tags for ``repo``: the registry first, GitHub releases as fallback."""
    tags = DockerContainer.list_remote_tags(repo)
    if tags:
        return tags
    return release_tags(repo, auth_token=auth_token)


def release_tags(repo: str, *, auth_token: str | None = None) -> list[str]:
    """Image tags named in the release bodies of the repo's GitHub project.

    Only GHCR repos have a matching GitHub project under the same owner
    and name, so anything else yields nothing.
    """
    owner_repo = _ghcr_owner_repo(repo)
    if owner_repo is None:
        return []
    owner, name = owner_repo
    body = _get_json(f"{_GITHUB_API}/repos/{owner}/{name}/releases", auth_token=auth_token)
    if not isinstance(body, list):
        return []
    pattern = re.compile(re.escape(f"{repo}:") + _TAG_PATTERN)
    tags: list[str] = []
    seen: set[str] = set()
    for release in body:
        if not isinstance(release, dict):
            continue
        for tag in pattern.findall(release.get("body") or ""):
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


def _ghcr_owner_repo(repo: str) -> tuple[str, str] | None:
    prefix = "ghcr.io/"
    if not repo.startswith(prefix):
        return None
    parts = repo[len(prefix) :].strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def tag_cache_path(cache_root: Path, repo: str) -> Path:
    return cache_root / "releases-cache" / f"{repo.replace('/', '_')}.json"


def read_tag_cache(path: Path, ttl_s: float | None) -> list[str] | None:
    """Cached tags, or None when absent, unreadable or past ``ttl_s``.

    ``ttl_s=None`` accepts a stale entry — used to keep the last known
    tag list alive while the registry is unreachable.
    """
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
    if ttl_s is not None and (time.time() - fetched_at) >= ttl_s:
        return None
    tags = data.get("tags")
    if not isinstance(tags, list):
        return None
    return [t for t in tags if isinstance(t, str)]


def write_tag_cache(path: Path, tags: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    payload = {"version": 1, "fetched_at": time.time(), "tags": tags}
    with tmp.open("w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def _get_json(url: str, *, auth_token: str | None) -> object | None:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    for delay in _RETRY_DELAYS_S:
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            continue
    return None


__all__ = [
    "list_image_tags",
    "read_tag_cache",
    "release_tags",
    "tag_cache_path",
    "write_tag_cache",
]
