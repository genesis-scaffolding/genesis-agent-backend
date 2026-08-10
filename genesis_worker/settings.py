"""Settings for the worker.

Nested pydantic-settings model. Per-source and per-service settings are
nested under ``SourcesSettings`` and ``ServicesSettings`` so adding a
new source or service is one new pydantic model and one new field — no
central enum.

Only the top-level ``Settings`` is a ``BaseSettings``. Nested models are
plain ``BaseModel``. The env-var delimiter is ``__`` so users can write
``GENESIS_PATHS__DATA_DIR=/foo`` to override a nested field.

Env-var precedence (lowest -> highest):

    1. class defaults
    2. dev.env (if present in cwd)
    3. .env   (if present in cwd)
    4. real env vars (GENESIS_*)
    5. explicit constructor args

ADR-004 details the path-resolution rules and the v1 backwards-compat
fallback chain for legacy state files.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import repo_root, xdg_path

# --- Path fields -------------------------------------------------------------


class PathsSettings(BaseModel):
    """XDG-aware path fields, with optional legacy-repo-root fallback."""

    data_dir: Path = Field(default_factory=lambda: xdg_path("DATA", ".local/share"))
    config_dir: Path = Field(default_factory=lambda: xdg_path("CONFIG", ".config"))
    cache_dir: Path = Field(default_factory=lambda: xdg_path("CACHE", ".cache"))
    state_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state"))
    log_dir: Path = Field(default_factory=lambda: xdg_path("STATE", ".local/state"))

    vault_path: Path | None = None

    @property
    def resolved_vault_path(self) -> Path:
        if self.vault_path is not None:
            return self.vault_path
        return self.data_dir / "vault"

    @property
    def resolved_repo_root(self) -> Path:
        return repo_root()


# --- Per-source settings -----------------------------------------------------


class HuggingFaceSourceSettings(BaseModel):
    local_path: Path | None = None
    default_revision: str = "main"


class LMSourceSettings(BaseModel):
    local_path: Path | None = None


class SourcesSettings(BaseModel):
    huggingface: HuggingFaceSourceSettings = Field(default_factory=HuggingFaceSourceSettings)
    lmstudio: LMSourceSettings = Field(default_factory=LMSourceSettings)


# --- Per-service settings ----------------------------------------------------


class LlamaSwapServiceSettings(BaseModel):
    config_path: Path | None = None
    recipes_path: Path | None = None
    listen_addr: str = "127.0.0.1:8080"
    session_name: str = "swap"
    log_file: Path | None = None
    health_timeout_s: float = 60.0
    kv_quant_over_bytes: int = 25_000_000_000
    mmproj_offload_over_bytes: int = 25_000_000_000
    default_binary_rel: str = "vendor/llama.cpp/build/bin/llama-server"


class ServicesSettings(BaseModel):
    llama_swap: LlamaSwapServiceSettings = Field(default_factory=LlamaSwapServiceSettings)


# --- Top-level settings ------------------------------------------------------


class Settings(BaseSettings):
    """Runtime configuration for the Genesis Worker."""

    model_config = SettingsConfigDict(
        env_prefix="GENESIS_",
        env_nested_delimiter="__",
        env_file=("dev.env", ".env"),
        extra="ignore",
    )

    paths: PathsSettings = Field(default_factory=PathsSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    services: ServicesSettings = Field(default_factory=ServicesSettings)


__all__ = [
    "HuggingFaceSourceSettings",
    "LMSourceSettings",
    "LlamaSwapServiceSettings",
    "PathsSettings",
    "ServicesSettings",
    "Settings",
    "SourcesSettings",
]
