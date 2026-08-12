"""Framework settings. Plugin option slices are opaque here — see ADR-009."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import repo_root, xdg_path

# The directory the worker owns under each XDG base. Change it here to rename them all.
XDG_BASE = "genesis-worker"


@lru_cache(maxsize=1)
def _read_models_root() -> str | None:
    """Read ``MODELS_ROOT`` from os.environ or the repo-root ``.env``.

    pydantic-settings reads ``.env`` via dotenv but doesn't populate
    ``os.environ``, so a value in ``.env`` is invisible to naive env-var
    reads. We check both places.
    """
    if "MODELS_ROOT" in os.environ:
        return os.environ["MODELS_ROOT"]
    try:
        from dotenv import dotenv_values

        values = dotenv_values(".env")
    except Exception:  # noqa: BLE001 — no .env or unreadable; fall through
        return None
    return values.get("MODELS_ROOT") or None


class PathsSettings(BaseModel):
    data_dir: Path = Field(default_factory=lambda: xdg_path("DATA", ".local/share", XDG_BASE))
    config_dir: Path = Field(default_factory=lambda: xdg_path("CONFIG", ".config", XDG_BASE))
    cache_dir: Path = Field(default_factory=lambda: xdg_path("CACHE", ".cache", XDG_BASE))
    state_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state", XDG_BASE))
    log_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state", XDG_BASE))

    vault_path: Path | None = None

    @property
    def resolved_vault_path(self) -> Path:
        if self.vault_path is not None:
            return self.vault_path
        # Backward-compat: legacy `bin/` scripts (and users with existing
        # `.env` files from the pre-framework era) set ``MODELS_ROOT``. Honour
        # it as a synonym for the vault root so migration is silent.
        legacy = _read_models_root()
        if legacy is not None:
            return Path(legacy)
        return self.data_dir / "vault"

    @property
    def resolved_repo_root(self) -> Path:
        return repo_root()


class Settings(BaseSettings):
    """Runtime configuration for the Genesis Worker.

    ``sources`` and ``services`` map a plugin name to its option slice. The
    framework never reads inside a slice; the plugin parses it at construction.
    """

    model_config = SettingsConfigDict(
        env_prefix="GENESIS_",
        env_nested_delimiter="__",
        env_file=("dev.env", ".env"),
        extra="ignore",
    )

    paths: PathsSettings = Field(default_factory=PathsSettings)
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    services: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def options_for(self, axis: str, name: str) -> dict[str, Any]:
        return dict(getattr(self, axis).get(name, {}))


__all__ = [
    "PathsSettings",
    "Settings",
]
