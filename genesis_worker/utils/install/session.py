"""Background install session — streaming state-machine base for plugin install backends."""

from __future__ import annotations

import threading
from abc import abstractmethod
from dataclasses import dataclass

from ...contracts import AcquireChoice, AcquireStateKind, AcquireView, InstallSession
from ..background_session import _Canceled


@dataclass
class _SessionState:
    step: AcquireView
    canceled: bool = False
    done: bool = False


class BackgroundInstallSession(InstallSession):
    """Streaming install session with a daemon worker thread.

    Subclasses implement ``_run_inner()`` to perform the actual install
    (download, uv install, etc.). The thread handles cancellation via
    ``self._cancel: threading.Event``.
    """

    def __init__(self) -> None:
        self._state = _SessionState(
            step=AcquireView(kind=AcquireStateKind.FETCHING, title=f"installing {self._name}")
        )
        self._cancel = threading.Event()
        self._done = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    @abstractmethod
    def _name(self) -> str:
        """Human-readable name of the thing being installed.

        Used in the initial step title.
        """

    @abstractmethod
    def _run_inner(self) -> None:
        """Perform the install.

        Check ``self._cancel.is_set()`` between long-running steps (e.g.
        between files, between fetch and extract).

        On success: call ``self._publish(AcquireView(kind='complete', ...))``.
        On failure: raise an exception (any subclass of ``Exception``).
        On cancellation: raise ``_Canceled``.

        The thread supervisor catches all of these and publishes the
        appropriate step.
        """

    def _publish(self, step: AcquireView) -> None:
        self._state.step = step

    def _run(self) -> None:
        try:
            self._run_inner()
        except _Canceled:
            self._publish(
                AcquireView(kind=AcquireStateKind.CANCELLED, title="cancelled", can_cancel=False)
            )
        except Exception as exc:  # noqa: BLE001
            self._publish(
                AcquireView(
                    kind=AcquireStateKind.FAILED,
                    title=f"install failed: {exc}",
                    error=str(exc),
                )
            )
        finally:
            self._state.done = True

    # InstallSession protocol

    def current_step(self) -> AcquireView:
        return self._state.step

    def submit(self, choice: AcquireChoice) -> AcquireView:
        return self._state.step

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self) -> AcquireView:
        self._thread.join()
        return self._state.step


__all__ = ["BackgroundInstallSession", "_Canceled"]
