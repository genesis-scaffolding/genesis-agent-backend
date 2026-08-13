"""Context factories for plugin tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genesis_worker.contracts import (
    NoSecretsAccessor,
    SecretsAccessor,
    ServiceContext,
    SourceContext,
)


def _dirs(root: Path) -> dict[str, Path]:
    return {
        "data_dir": root / "data",
        "config_dir": root / "config",
        "cache_dir": root / "cache",
        "state_dir": root / "state",
        "log_dir": root / "log",
    }


def source_ctx(
    root: Path | None = None,
    *,
    name: str = "test-source",
    local_path: Path | None = None,
    options: dict[str, Any] | None = None,
    secrets: SecretsAccessor | None = None,
) -> SourceContext:
    root = root if root is not None else (local_path or Path("."))
    return SourceContext(
        name=name,
        repo_root=root,
        options=options or {},
        local_path=local_path if local_path is not None else root / "vault",
        vault_path=root / "vault",
        secrets=secrets if secrets is not None else NoSecretsAccessor(),
        **_dirs(root),
    )


def service_ctx(
    root: Path,
    *,
    name: str = "test-service",
    options: dict[str, Any] | None = None,
    secrets: SecretsAccessor | None = None,
) -> ServiceContext:
    return ServiceContext(
        name=name,
        repo_root=root,
        options=options or {},
        secrets=secrets if secrets is not None else NoSecretsAccessor(),
        **_dirs(root),
    )


__all__ = ["service_ctx", "source_ctx"]
