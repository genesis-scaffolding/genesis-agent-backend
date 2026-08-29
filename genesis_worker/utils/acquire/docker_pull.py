"""Acquire session for ``docker pull``-backed installs.

Streams ``docker pull --progress=json`` output through
:class:`~genesis_worker.utils.process.docker_pull_progress.DockerPullProgress`
and publishes each line as an :class:`AcquireView`. The Status page's
existing ``st.progress`` branch in ``ui/image.py`` renders the bar
when ``step.progress is not None``; no UI changes required.

See ADR-028.
"""

from __future__ import annotations

from collections.abc import Callable

from ...contracts import (
    AcquireChoice,
    AcquireProgress,
    AcquireState,
    AcquireStateKind,
    AcquireView,
)
from ..background_session import BackgroundSession, _Canceled
from ..process import DockerContainer
from ..process.docker_pull_progress import DockerPullProgress


class DockerPullAcquireSession(BackgroundSession):
    """Streaming acquire session backed by ``docker pull``.

    Construction:
        image       — fully-qualified image ref, e.g. ``ghcr.io/foo/bar:v1``.
        on_complete — optional callback invoked after a successful pull,
                      used by the service to record the selection file.

    Eager: the worker thread starts in ``__init__``; there are no
    interactive steps before the pull.
    """

    source_name = "docker_pull"

    def __init__(
        self,
        *,
        image: str,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        state = AcquireState(
            kind=AcquireStateKind.FETCHING,
            repo_id=image,
        )
        super().__init__(state)
        self._image = image
        self._on_complete = on_complete
        self._parser = DockerPullProgress()
        self._start()

    @property
    def repo_id(self) -> str:
        return self._state.repo_id

    def view(self) -> AcquireView:
        kind = self._state.kind
        if kind == AcquireStateKind.FETCHING:
            tail = self._state.log_tail[-20:]
            return AcquireView(
                kind=kind,
                title=f"Pulling {self._image}",
                progress=AcquireProgress(
                    bytes_done=self._state.bytes_done,
                    bytes_total=self._state.bytes_total,
                    speed_bps=0,
                    eta_s=0,
                ),
                log_tail=tail,
                can_cancel=True,
            )
        if kind == AcquireStateKind.COMPLETE:
            return AcquireView(
                kind=kind,
                title=f"Pulled {self._image}",
                can_cancel=False,
            )
        if kind == AcquireStateKind.FAILED:
            tail = self._state.log_tail[-20:]
            return AcquireView(
                kind=kind,
                title=f"Failed: {self._image}",
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
        return AcquireView(kind=kind, title=f"Pulling {self._image}", can_cancel=True)

    def submit(self, choice: AcquireChoice) -> None:
        # Pipelines don't have interactive steps; submit is a no-op.
        return None

    def _run_inner(self) -> None:
        try:
            DockerContainer.pull(
                self._image,
                progress=self._on_progress,
                cancel=self._cancel_event.is_set,
                progress_format="json",
            )
        except _Canceled:
            raise
        except RuntimeError as exc:
            raise RuntimeError(str(exc)) from exc
        if self._on_complete is not None:
            self._on_complete()
        self._state.kind = AcquireStateKind.COMPLETE

    def _on_progress(self, line: str) -> None:
        if self._cancel_event.is_set():
            raise _Canceled
        self._parser.update(line)
        snap = self._parser.snapshot()
        self._state.bytes_done = snap.bytes_done
        self._state.bytes_total = snap.bytes_total


__all__ = ["DockerPullAcquireSession"]
