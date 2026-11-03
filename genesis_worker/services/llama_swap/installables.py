"""Llama-swap installable seeds and GitHub release helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from ...utils.installables import (
    InstallableRecord,
    ReleaseAsset,
    ReleaseCandidate,
    parse_installable_record,
)

BUNDLED_INSTALLABLES_PATH = Path(__file__).parent / "data" / "installables.yaml"

_REPO_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".zip")
_EXCLUDED_ASSET_MARKERS = (
    "win",
    "macos",
    "android",
    "arm64",
    "aarch64",
    "s390x",
    "riscv",
    "cudart",
    "ui.tar",
    "xcframework",
)


def load_seed_records(path: Path = BUNDLED_INSTALLABLES_PATH) -> list[InstallableRecord]:
    """Load and validate the bundled installable seed records."""
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"installables seed file {path}: {exc}") from exc
    items = (raw or {}).get("installables") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise TypeError(f"installables seed file {path}: expected an installables list")
    records: list[InstallableRecord] = []
    for item in items:
        try:
            records.append(parse_installable_record(item))
        except ValidationError as exc:
            raise RuntimeError(f"installables seed file {path}: {exc}") from exc
    return records


def parse_repo_url(raw: str) -> tuple[str, str]:
    """Parse a GitHub repository URL or slug into ``(owner, repo)``."""
    value = raw.strip()
    owner = ""
    repo = ""
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    elif value.startswith("https://github.com/"):
        path = urlparse(value).path
    elif "://" not in value and value.count("/") == 1:
        path = value
    else:
        path = ""

    path = path.strip("/").removesuffix(".git")
    parts = path.split("/")
    if len(parts) >= 3 and parts[2:] in (["releases"], ["releases", "latest"]):
        parts = parts[:2]
    if len(parts) == 2:
        owner, repo = parts

    if not owner or not repo:
        raise ValueError(
            "expected a GitHub repository URL (https://github.com/<owner>/<repo>), "
            "an SSH URL (git@github.com:<owner>/<repo>.git), or <owner>/<repo>"
        )
    if not _REPO_PART_RE.fullmatch(owner) or not _REPO_PART_RE.fullmatch(repo):
        raise ValueError(f"invalid GitHub repository {raw!r}")
    return owner, repo


def fork_slug(owner: str, repo: str) -> str:
    """Turn repository coordinates into a lowercase dashed slug."""
    return re.sub(r"[^a-z0-9]+", "-", f"{owner}/{repo}".lower()).strip("-")


def guess_backend(asset_name: str) -> str:
    """Guess the accelerator backend from a release asset name."""
    name = asset_name.lower()
    if "cuda" in name:
        return "cuda"
    if "vulkan" in name:
        return "vulkan"
    if "rocm" in name or "hip" in name:
        return "rocm"
    return "cpu"


def candidate_assets(assets: list[dict[str, Any]], tag: str) -> list[ReleaseAsset]:
    """Select Linux x64 archive assets that can install a llama-server binary."""
    candidates: list[ReleaseAsset] = []
    for asset in assets:
        name = str(asset.get("name", ""))
        lowered = name.lower()
        url = asset.get("browser_download_url")
        if not url or not lowered.endswith(_ARCHIVE_SUFFIXES):
            continue
        if "x64" not in lowered and "amd64" not in lowered:
            continue
        if any(marker in lowered for marker in _EXCLUDED_ASSET_MARKERS):
            continue
        raw_size = asset.get("size")
        size = int(raw_size) if isinstance(raw_size, int) else None
        candidates.append(ReleaseAsset(name=name, url=str(url), size_bytes=size))
    return candidates


def release_candidate(repo: str, tag: str, assets: list[ReleaseAsset]) -> ReleaseCandidate:
    """Build the release metadata returned by the service."""
    return ReleaseCandidate(repo=repo, tag=tag, assets=tuple(assets))


__all__ = [
    "BUNDLED_INSTALLABLES_PATH",
    "candidate_assets",
    "fork_slug",
    "guess_backend",
    "load_seed_records",
    "parse_repo_url",
    "release_candidate",
]
