"""Framework settings. Plugin option slices are opaque here — see ADR-009."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import repo_root, xdg_path


def _xdg(name: str, default_relative_to_home: str) -> Path:
    # Resolved at call time so PathsSettings.XDG_BASE stays the one source of truth.
    return xdg_path(name, default_relative_to_home, PathsSettings.XDG_BASE)


class PathsSettings(BaseModel):
    # The directory the worker owns under each XDG base. Change it here.
    # ClassVar, not a field: renaming it is a code decision, not a user setting.
    XDG_BASE: ClassVar[str] = "genesis-worker"

    data_dir: Path = Field(default_factory=lambda: _xdg("DATA", ".local/share"))
    config_dir: Path = Field(default_factory=lambda: _xdg("CONFIG", ".config"))
    cache_dir: Path = Field(default_factory=lambda: _xdg("CACHE", ".cache"))
    state_dir: Path = Field(default_factory=lambda: _xdg("STATE", ".local/state"))
    log_dir: Path = Field(default_factory=lambda: _xdg("STATE", ".local/state"))

    vault_path: Path | None = None

    @property
    def resolved_vault_path(self) -> Path:
        if self.vault_path is not None:
            return self.vault_path
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
