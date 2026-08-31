"""Acquire session for ``uv tool install``-backed installs.

The session runs ``uv tool install <package>==<version>`` (or
``<package>@latest`` when no version is pinned) and reports
``FETCHING → COMPLETE``. uv is the source of truth for what is
installed; the service reads ``uv tool list`` for ``installed_version``.

See ADR-028.
"""

from __future__ import annotations

import shutil
import subprocess

from ...contracts import (
    AcquireChoice,
    AcquireState,
    AcquireStateKind,
    AcquireView,
)
from ..background_session import BackgroundSession, _Canceled

_DEFAULT_TIMEOUT_S = 300.0


class UvToolAcquireSession(BackgroundSession):
    """Streaming acquire session backed by ``uv tool install``.

    Construction:
        package_name — the PyPI package to install, e.g. ``cptr``.
        version      — explicit version, or ``None`` to use ``@latest``.

    Eager: the worker thread starts in ``__init__``; there are no
    interactive steps before the install.
    """

    source_name = "uv_tool"

    def __init__(
        self,
        *,
        package_name: str,
        version: str | None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        spec = f"{package_name}=={version}" if version else f"{package_name}@latest"
        state = AcquireState(
            kind=AcquireStateKind.FETCHING,
            repo_id=spec,
        )
        super().__init__(state)
        self._package_name = package_name
        self._version = version
        self._timeout_s = timeout_s
        self._start()

    @property
    def repo_id(self) -> str:
        return self._state.repo_id

    def view(self) -> AcquireView:
        kind = self._state.kind
        repo_id = self._state.repo_id
        if kind == AcquireStateKind.FETCHING:
            tail = self._state.log_tail[-20:]
            return AcquireView(
                kind=kind,
                title=f"Installing {repo_id}",
                log_tail=tail,
                can_cancel=True,
            )
        if kind == AcquireStateKind.COMPLETE:
            return AcquireView(
                kind=kind,
                title=f"Installed {repo_id}",
                can_cancel=False,
            )
        if kind == AcquireStateKind.FAILED:
            tail = self._state.log_tail[-20:]
            return AcquireView(
                kind=kind,
                title=f"Failed: {repo_id}",
                error=self._state.failure,
                log_tail=tail,
                can_cancel=False,
            )
        if kind == AcquireStateKind.CANCELLED:
            return AcquireView(
                kind=kind,
                title="Cancelled",
                can_cancel=False,
            )
        return AcquireView(kind=kind, title=f"Installing {repo_id}", can_cancel=True)

    def submit(self, choice: AcquireChoice) -> None:
        # Pipelines don't have interactive steps; submit is a no-op.
        return None

    def _run_inner(self) -> None:
        spec = (
            f"{self._package_name}=={self._version}"
            if self._version
            else f"{self._package_name}@latest"
        )
        if self._cancel_event.is_set():
            raise _Canceled

        try:
            result = subprocess.run(
                ["uv", "tool", "install", spec],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_s,
            )
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(f"uv not found on PATH: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"uv tool install timed out after {self._timeout_s:.0f}s") from exc

        if self._cancel_event.is_set():
            raise _Canceled

        if result.returncode != 0:
            raise RuntimeError(
                f"uv tool install failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip() or 'no output'}"
            )

        if shutil.which(self._package_name) is None:
            raise RuntimeError(
                f"{self._package_name} binary not on PATH after install — is ~/.local/bin on PATH?"
            )

        self._state.kind = AcquireStateKind.COMPLETE


__all__ = ["UvToolAcquireSession"]
