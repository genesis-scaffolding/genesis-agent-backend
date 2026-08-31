"""HTTP readiness probing — single probe and polling loop."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import ClassVar

_DEFAULT_POLL_S = 1.0
_DEFAULT_PROBE_TIMEOUT_S = 1.0


class HealthProbe:
    """HTTP readiness probe with polling.

    ``probe()`` issues a single synchronous HTTP request.
    ``wait_ready()`` polls it until the timeout expires.
    ``resolve_connect_host()`` translates bind addresses (0.0.0.0, ::)
    to connectable addresses (127.0.0.1).
    """

    DEFAULT_PROBE_PATH: ClassVar[str] = "/v1/models"

    def __init__(
        self,
        host: str,
        port: int,
        *,
        probe_path: str = DEFAULT_PROBE_PATH,
    ) -> None:
        self._host = host
        self._port = port
        self._probe_path = probe_path

    @staticmethod
    def resolve_connect_host(host: str) -> str:
        """Translate a bind address into a connectable address.

        ``0.0.0.0`` and ``::`` are bind-only; clients must connect via
        ``127.0.0.1``. All other hosts are returned unchanged.
        """
        if host in ("0.0.0.0", "::"):
            return "127.0.0.1"
        return host

    @property
    def endpoint(self) -> str:
        """Base URL without the probe path, e.g. ``http://host:8080/``."""
        return f"http://{self._host}:{self._port}/"

    def _url(self) -> str:
        return f"http://{self.resolve_connect_host(self._host)}:{self._port}{self._probe_path}"

    def probe(self) -> bool:
        """Single synchronous probe. Returns True iff the response is HTTP 200."""
        try:
            with urllib.request.urlopen(self._url(), timeout=_DEFAULT_PROBE_TIMEOUT_S) as resp:
                return resp.status == 200
        except (
            urllib.error.URLError,
            ConnectionError,
            TimeoutError,
            OSError,
        ):
            return False

    def wait_ready(self, timeout_s: float) -> bool:
        """Poll ``probe()`` until it returns True or ``timeout_s`` elapses."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.probe():
                return True
            time.sleep(_DEFAULT_POLL_S)
        return False


__all__ = ["HealthProbe"]
