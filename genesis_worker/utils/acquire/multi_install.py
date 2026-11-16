"""One acquire session that installs several installables in order.

A compose stack needs every image it runs. The service-level install
(ADR-049) is this session over the service's installables: each one is
skipped when its artifact is already present, installed at its recorded
version when there is one, and a failure names the artifact that failed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ...contracts import (
    AcquireChoice,
    AcquireSession,
    AcquireState,
    AcquireStateKind,
    AcquireView,
    ServiceInstall,
)
from ..background_session import BackgroundSession, _Canceled
from ..refs import installable_ref


class MultiInstallSession(BackgroundSession):
    """Installs a list of installables, one after another, in one session.

    Eager: the worker thread starts in ``__init__`` — the sessions it
    drives are eager too, and the caller (the Install button, the
    install endpoint) only builds this when the install should happen.
    """

    source_name = "multi_install"

    def __init__(self, installables: Sequence[ServiceInstall], *, label: str = "") -> None:
        state = AcquireState(
            kind=AcquireStateKind.FETCHING,
            repo_id=label or "multi-install",
        )
        super().__init__(state)
        self._installables = list(installables)
        self._label = label
        self._current: AcquireSession | None = None
        self._index = 0
        self._failed_ref: str | None = None
        self._start()

    @property
    def repo_id(self) -> str:
        return self._state.repo_id

    @property
    def _total(self) -> int:
        return len(self._installables)

    def view(self) -> AcquireView:
        kind = self._state.kind
        if kind == AcquireStateKind.COMPLETE:
            return AcquireView(kind=kind, title=self._complete_title(), can_cancel=False)
        if kind == AcquireStateKind.FAILED:
            return AcquireView(
                kind=kind,
                title=f"Failed: Pulling {self._failed_ref or self._label}",
                error=self._state.failure,
                can_cancel=False,
            )
        if kind == AcquireStateKind.CANCELLED:
            return AcquireView(kind=kind, title="Cancelled", can_cancel=False)
        current = self._current
        if current is not None:
            sub = current.view()
            return replace(sub, title=f"{sub.title} ({self._index}/{self._total})")
        return AcquireView(kind=kind, title=f"Installing {self._label}", can_cancel=True)

    def submit(self, choice: AcquireChoice) -> None:
        # Pipelines don't have interactive steps; submit is a no-op.
        return None

    def cancel(self) -> None:
        super().cancel()
        current = self._current
        if current is not None:
            current.cancel()

    def _run_inner(self) -> None:
        for index, installable in enumerate(self._installables, start=1):
            if self._cancel_event.is_set():
                raise _Canceled
            ref = installable_ref(installable)
            if installable.is_present():
                self._append_log(f"{ref}: already present")
                continue
            self._index = index
            self._failed_ref = ref
            # The recorded selection wins: a version pick made on the
            # Images page is never clobbered by the one-click install.
            session = installable.install(version=installable.installed_version())
            self._current = session
            try:
                final = session.wait()
            finally:
                self._current = None
            if final.kind == AcquireStateKind.FAILED:
                raise RuntimeError(f"install of {ref} failed: {final.error or 'unknown error'}")
            if final.kind == AcquireStateKind.CANCELLED:
                raise _Canceled
            self._failed_ref = None
        self._state.kind = AcquireStateKind.COMPLETE

    def _complete_title(self) -> str:
        refs = [installable_ref(installable) for installable in self._installables]
        noun = "image" if len(refs) == 1 else "images"
        return f"Pulled {', '.join(refs)} ({len(refs)} {noun})"


__all__ = ["MultiInstallSession"]
