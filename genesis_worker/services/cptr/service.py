"""cptr (Open WebUI Computer) inference service plugin."""

from __future__ import annotations

import socket
from pathlib import Path

from ...contracts import (
    InferenceService,
    ServiceCapabilities,
    ServiceCategory,
    ServiceContext,
    ServiceInstall,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)
from . import lifecycle
from .install import CptrInstall
from .options import CptrOptions


class CptrService(InferenceService):
    """Inference-service-shaped plugin for Open WebUI Computer."""

    name = "cptr"
    display_name = "Open WebUI Computer"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        self._options = CptrOptions(**ctx.options)
        self._log_file = self._options.log_file or ctx.log_dir / "cptr.log"
        self._install = CptrInstall()

    def is_available(self) -> bool:
        return self._install.binary_path() is not None

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        )

    @property
    def category(self) -> ServiceCategory:
        return ServiceCategory.CHAT

    @property
    def description(self) -> str:
        return "Open WebUI automation"

    def installs(self) -> list[ServiceInstall]:
        return [self._install]

    def primary_installable(self) -> ServiceInstall | None:
        return self._install

    def uninstall_installable(self, name: str, *, version: str | None = None) -> None:
        """Remove an installable's installed version. Refuses if the service is running.

        Mirrors the llama-swap guard so deleting the on-disk artifact while
        the process is running doesn't succeed silently.
        """
        if self.is_running():
            raise RuntimeError(
                f"cannot uninstall {name!r} while {self.display_name} is running — "
                "stop the service first"
            )
        for installable in self.installs():
            if installable.name == name:
                installable.uninstall(version=version)
                return
        raise KeyError(f"unknown installable {name!r}")

    # --- Lifecycle ---------------------------------------------------------

    def is_running(self) -> bool:
        return lifecycle.is_running(self._options.session_name)

    def tail_log(self, n_bytes: int = 8192) -> str:
        """Return the last ``n_bytes`` of the log file, or "" if missing.

        The lifecycle pipes cptr's stdout/stderr into the log via
        ``tee -a``, so this is the canonical source for the process's
        console output.
        """
        if not self._log_file.is_file():
            return ""
        with self._log_file.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            return f.read().decode("utf-8", errors="replace")

    def public_host(self) -> str:
        """Hostname clients should use to reach this service."""
        if self._options.public_host:
            return self._options.public_host
        try:
            return socket.gethostname()
        except OSError:
            return "localhost"

    @property
    def listen_address(self) -> str:
        return f"{self._options.listen_host}:{self._options.listen_port}"

    @property
    def installed_version(self) -> str | None:
        """The version uv currently has installed for cptr."""
        return self._install.installed_version()

    def runtime_endpoint(self) -> str | None:
        """cptr is web-UI only — no OpenAI-compatible API to point at."""
        return None

    def web_ui_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        return f"http://{self.public_host()}:{self._options.listen_port}/"

    def start(self) -> StartResult:
        binary = self._install.binary_path()
        if binary is None:
            return StartResult(ok=False, message="cptr binary not installed")
        return lifecycle.start_cptr(
            binary=binary,
            host=self._options.listen_host,
            port=self._options.listen_port,
            session_name=self._options.session_name,
            log_file=self._log_file,
            health_timeout_s=self._options.health_timeout_s,
            stream_timeout_s=lifecycle._STREAM_READ_TIMEOUT_S,
        )

    def stop(self) -> StopResult:
        return lifecycle.stop_cptr(self._options.session_name)

    def status(self) -> ServiceStatus:
        return lifecycle.status(
            self._options.session_name,
            self._options.listen_host,
            self._options.listen_port,
        )

    def wait_ready(self, timeout_s: float) -> bool:
        return lifecycle.wait_ready(
            self._options.listen_host,
            self._options.listen_port,
            timeout_s,
        )

    def resource_estimate(self) -> ServiceResourceEstimate:
        return ServiceResourceEstimate(
            vram_bytes_typical=0,
            vram_bytes_min=0,
            cpu_cores_recommended=2,
        )

    @property
    def ui_pages(self) -> list[UiPage]:
        ui_dir = Path(__file__).parent / "ui"
        return [
            # ``url_path`` is explicit because both llama_swap's status
            # page and this one are named ``status.py`` — without an
            # explicit slug, Streamlit infers ``/status`` for both and
            # refuses to start.
            UiPage(
                "Status",
                ":material/monitor:",
                ui_dir / "status.py",
                url_path="cptr_status",
            ),
        ]


__all__ = ["CptrService"]
