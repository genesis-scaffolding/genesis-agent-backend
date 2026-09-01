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
from genesis_worker.contracts.host import HostInfo


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
    vault_path: Path | None = None,
    options: dict[str, Any] | None = None,
    secrets: SecretsAccessor | None = None,
    host_info: HostInfo | None = None,
) -> SourceContext:
    root = root if root is not None else (local_path or Path("."))
    resolved_local = local_path if local_path is not None else root / "vault"
    resolved_vault = vault_path if vault_path is not None else root / "vault"
    return SourceContext(
        name=name,
        repo_root=root,
        options=options or {},
        local_path=resolved_local,
        vault_path=resolved_vault,
        host_info=host_info if host_info is not None else HostInfo.empty(),
        secrets=secrets if secrets is not None else NoSecretsAccessor(),
        **_dirs(root),
    )


def service_ctx(
    root: Path,
    *,
    name: str = "test-service",
    vault_path: Path | None = None,
    options: dict[str, Any] | None = None,
    secrets: SecretsAccessor | None = None,
    host_info: HostInfo | None = None,
) -> ServiceContext:
    return ServiceContext(
        name=name,
        repo_root=root,
        vault_path=vault_path if vault_path is not None else root / "vault",
        options=options or {},
        host_info=host_info if host_info is not None else HostInfo.empty(),
        secrets=secrets if secrets is not None else NoSecretsAccessor(),
        **_dirs(root),
    )


__all__ = ["service_ctx", "source_ctx"]
