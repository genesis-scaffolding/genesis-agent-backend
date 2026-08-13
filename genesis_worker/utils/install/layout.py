"""Disk layout for one ServiceInstall (ADR-012).

Each installable gets its own ``InstallLayout`` rooted at
``<data_dir>/installs/<name>/``. The default selection is the
``current`` symlink; ``<state_dir>/selections.yaml`` may pin a
specific version (UI is read-only in v1).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import yaml


class InstallLayout:
    def __init__(self, data_dir: Path, state_dir: Path, name: str) -> None:
        self._installs_root = data_dir / "installs" / name
        self._selections_path = state_dir / "selections.yaml"
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def installs_root(self) -> Path:
        return self._installs_root

    @property
    def selections_path(self) -> Path:
        return self._selections_path

    @property
    def current_symlink(self) -> Path:
        return self._installs_root / "current"

    def manifest_path(self, version: str) -> Path:
        return self._installs_root / version / "MANIFEST"

    def binary_path(self, version: str, binary_rel: str) -> Path:
        return self._installs_root / version / binary_rel

    def resolved_selection(self) -> str | None:
        """The version whose binary should be used. None when nothing is selected.

        Resolution order: ``selections.yaml`` pin (must be installed) →
        ``current`` symlink target → None.
        """
        pinned = self._read_pin()
        if pinned is not None and (self._installs_root / pinned).is_dir():
            return pinned
        link = self.current_symlink
        if link.is_symlink():
            target = os.readlink(link)
            if not os.path.isabs(target):
                return target
        return None

    def set_selection(self, version: str) -> None:
        """Pin ``version`` in ``selections.yaml``. Atomic write."""
        if not (self._installs_root / version).is_dir():
            raise ValueError(f"cannot pin to uninstalled version {version!r}")
        self._selections_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {}
        if self._selections_path.is_file():
            with self._selections_path.open() as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                data = {str(k): str(v) for k, v in loaded.items()}
        data[self._name] = version
        tmp = self._selections_path.with_name(
            f".selections.yaml.tmp.{os.getpid()}.{secrets.token_hex(4)}"
        )
        with tmp.open("w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=True)
        os.replace(tmp, self._selections_path)

    def set_current_symlink(self, version: str) -> None:
        """Atomically point ``current`` at ``<version>/``."""
        target = self._installs_root / version
        if not target.is_dir():
            raise ValueError(f"version {version!r} is not installed at {target}")
        self._installs_root.mkdir(parents=True, exist_ok=True)
        tmp = self._installs_root / f".current-tmp.{os.getpid()}.{secrets.token_hex(4)}"
        os.symlink(version, tmp)
        try:
            os.replace(tmp, self.current_symlink)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _read_pin(self) -> str | None:
        if not self._selections_path.is_file():
            return None
        with self._selections_path.open() as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return None
        value = data.get(self._name)
        return str(value) if value is not None else None


__all__ = ["InstallLayout"]
