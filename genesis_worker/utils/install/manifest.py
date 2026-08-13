"""MANIFEST sidecar for an installed binary (ADR-012)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ManifestSource:
    url: str


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    source: ManifestSource
    sha256: str | None = None
    verified: bool = False
    fetched_at: str = ""
    size_bytes: int | None = None
    install_method: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> Manifest:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        source = data.get("source") or {}
        return cls(
            name=data["name"],
            version=data["version"],
            source=ManifestSource(url=source.get("url", "")),
            sha256=data.get("sha256"),
            verified=bool(data.get("verified", False)),
            fetched_at=data.get("fetched_at", ""),
            size_bytes=data.get("size_bytes"),
            install_method=data.get("install_method", ""),
        )

    def to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            yaml.safe_dump(asdict(self), f, default_flow_style=False, sort_keys=True)


__all__ = ["Manifest", "ManifestSource"]
