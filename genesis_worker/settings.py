"""Framework settings. Plugin option slices are opaque here — see ADR-009.

Precedence (lowest → highest):

    defaults → dev.env → .env → real env → user-overrides.env → constructor args

``user-overrides.env`` lives at ``<config_dir>/user-overrides.env`` and
is layered as the topmost file source. ADR-034.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .contracts import SecretsAccessor
from .utils.config_overrides import read_user_overrides
from .utils.paths import repo_root, xdg_path

# The directory the worker owns under each XDG base. Change it here to rename them all.
XDG_BASE = "genesis-worker"

# Filename of the user-overrides file inside ``config_dir`` (ADR-034).
USER_OVERRIDES_FILENAME = "user-overrides.env"


@lru_cache(maxsize=1)
def _read_secret_key(key: str) -> str | None:
    """Read a secret from os.environ or the repo-root ``.env``.

    Cached for the process lifetime. ``Settings(secrets=SecretsSettings(...))``
    can override explicitly. Mirrors the existing ``_read_models_root``
    pattern: pydantic-settings reads ``.env`` for its own fields but does
    not populate ``os.environ``, so the dotenv fallback is required.
    """
    if key in os.environ:
        v = os.environ[key]
        return v if v else None
    try:
        from dotenv import dotenv_values

        values = dotenv_values(".env")
    except Exception:  # noqa: BLE001 — no .env or unreadable; fall through
        return None
    if not isinstance(values, dict):
        return None
    v = values.get(key)
    return v if v else None


class SecretsSettings(BaseModel):
    """Framework-managed secrets. Plugins ask via :class:`SecretsAccessor`.

    Bound to env via ``GENESIS_SECRETS__<KEY>`` (and the repo-root ``.env``
    via ``Settings.env_file`` plus the ``_read_secret_key`` fallback).
    """

    github_token: str | None = Field(
        default_factory=lambda: _read_secret_key("GENESIS_SECRETS__GITHUB_TOKEN")
    )

    def accessor(self) -> SecretsAccessor:
        return _SettingsSecretsAccessor(self)


class _SettingsSecretsAccessor(SecretsAccessor):
    """Wraps a :class:`SecretsSettings` for the framework plugin contract."""

    def __init__(self, settings: SecretsSettings) -> None:
        self._data = settings.model_dump()

    def get(self, name: str) -> str | None:
        value = self._data.get(name)
        if value is None or value == "":
            return None
        return str(value)


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
        return Path.home() / "models"

    @property
    def resolved_repo_root(self) -> Path:
        return repo_root()


class Settings(BaseSettings):
    """Runtime configuration for the Genesis Worker.

    ``sources`` and ``services`` map a plugin name to its option slice. The
    framework never reads inside a slice; the plugin parses it at construction.
    ``secrets`` is a typed store for framework-managed tokens (e.g. the
    GitHub PAT used by service-install code paths).
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
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)

    def __init__(self, **data: Any) -> None:
        # Layer the user-overrides file at the top of the precedence chain
        # (ADR-034). The default ``env_file`` in ``model_config`` is
        # ``("dev.env", ".env")``; we append the override file when present
        # so later files win. Pydantic-settings reads each entry in order.
        #
        # We resolve ``config_dir`` from the resolved env / constructor
        # kwargs rather than from ``data["paths"].config_dir`` because the
        # latter would skip the override file we're about to read.
        override_path = self._resolve_user_overrides_path(data)
        base_env_file_raw = self.model_config.get("env_file")
        if base_env_file_raw is None:
            base_env_file: tuple[str, ...] = ()
        elif isinstance(base_env_file_raw, str):
            base_env_file = (base_env_file_raw,)
        elif isinstance(base_env_file_raw, os.PathLike):
            base_env_file = (os.fspath(base_env_file_raw),)
        else:
            base_env_file = tuple(os.fspath(p) for p in base_env_file_raw)
        if override_path is not None and override_path.is_file():
            # Pre-parse to surface malformed lines loudly (ADR-034 — loud
            # failure beats silent corruption). Pydantic-settings itself
            # silently skips lines without ``=``; we want the user to know.
            read_user_overrides(override_path)
            base_env_file = (*base_env_file, str(override_path))
        super().__init__(**{**data, "_env_file": base_env_file})

    @staticmethod
    def _resolve_user_overrides_path(data: dict[str, Any]) -> Path | None:
        """Find ``<config_dir>/user-overrides.env`` from any of the override
        sources — constructor kwarg, real env, ``.env`` — in that order.

        The constructor calls this before ``super().__init__`` so the path
        resolution has to happen outside pydantic-settings. We mirror its
        env-walk by inspecting ``os.environ`` and the kwarg dict.
        """
        # Explicit kwarg wins (matches ADR precedence: constructor > everything).
        if "paths" in data and isinstance(data["paths"], PathsSettings):
            return data["paths"].config_dir / USER_OVERRIDES_FILENAME
        if os.environ.get("GENESIS_PATHS__CONFIG_DIR"):
            return Path(os.environ["GENESIS_PATHS__CONFIG_DIR"]) / USER_OVERRIDES_FILENAME
        # Fall back to XDG default.
        return xdg_path("CONFIG", ".config", XDG_BASE) / USER_OVERRIDES_FILENAME

    def options_for(self, axis: str, name: str) -> dict[str, Any]:
        return dict(getattr(self, axis).get(name, {}))

    def secret(self, name: str) -> str | None:
        """Read a framework-managed secret by name.

        Returns ``None`` when the secret is unset. Plugins should use
        ``ctx.secrets.get(name)`` rather than calling this directly.
        """
        return self.secrets.accessor().get(name)


def user_overrides_path(settings_paths: PathsSettings) -> Path:
    """The framework-level path to the user-overrides file. Helpers and the
    facade call this so the file's location has exactly one definition.

    Reads the resolved ``config_dir`` from a fully-built ``PathsSettings``
    — callers that don't have one yet should use the helper above.
    """
    return settings_paths.config_dir / USER_OVERRIDES_FILENAME


__all__ = [
    "PathsSettings",
    "SecretsAccessor",
    "SecretsSettings",
    "Settings",
    "user_overrides_path",
]
