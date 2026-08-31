"""Crawl4AI inference service — a Docker-container web crawler + REST API."""

from __future__ import annotations

import os
import secrets
import socket
from pathlib import Path

from ...contracts import (
    InferenceService,
    InstallState,
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
from .install import Crawl4AiImage
from .options import Crawl4AiOptions


class Crawl4AiService(InferenceService):
    """Service for Crawl4AI, running as a Docker container.

    Crawl4AI is a web crawler / scraper with an embedded dashboard, not an
    inference backend: it reports ``has_web_ui`` / ``can_install`` but
    ``can_serve_llm`` is False. There is no host-GPU dependency.
    """

    name = "crawl4ai"
    display_name = "Crawl4AI"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        opts = Crawl4AiOptions(**ctx.options)
        self._options = opts

        # Path defaults derived from ctx (ADR-025). ctx.*_dir are already
        # scoped to this service; do not re-append the service name here.
        self._data_path = opts.data_path or ctx.data_dir / "data"
        self._log_file = opts.log_file or ctx.log_dir / "crawl4ai.log"
        # Persistent auto-generated API token lives here so it survives
        # image upgrades and reinstalls. See ``_resolve_or_generate_api_token``.
        self._api_token_path = ctx.state_dir / "api_token"

        # PUID/PGID auto-default to the host user, like comfyui/sillytavern.
        self._puid = opts.puid if opts.puid is not None else os.getuid()
        self._pgid = opts.pgid if opts.pgid is not None else os.getgid()

        # Installable.
        self._install = Crawl4AiImage(
            data_dir=ctx.data_dir,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            image_repo=opts.image_repo,
            image_tag=opts.image_tag,
            secrets=ctx.secrets,
        )

    # --- introspection ----------------------------------------------------

    @property
    def image_ref(self) -> str:
        return f"{self._options.image_repo}:{self._options.image_tag}"

    @property
    def listen_address(self) -> str:
        return f"{self._options.listen_host}:{self._options.listen_port}"

    @property
    def log_file(self) -> Path:
        return self._log_file

    # --- contract overrides -----------------------------------------------

    def is_available(self) -> bool:
        # There is no host binary for a container service.
        return self._install.state() == InstallState.INSTALLED

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
        return ServiceCategory.CRAWLER

    @property
    def description(self) -> str:
        return "Web crawler + dashboard"

    def resource_estimate(self) -> ServiceResourceEstimate:
        # No GPU; Crawl4AI is a Python crawler/UI, light on resources.
        return ServiceResourceEstimate(
            vram_bytes_typical=0,
            vram_bytes_min=0,
            cpu_cores_recommended=2,
        )

    def is_running(self) -> bool:
        return lifecycle.is_running_crawl4ai(self._options.container_name)

    def runtime_endpoint(self) -> str | None:
        return None

    def web_ui_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        # The upstream's API root at ``/`` is auth-gated; the dashboard
        # itself lives at ``/playground/`` and serves 200 without a token.
        return f"http://{self.public_host()}:{self._options.listen_port}/playground/"

    def tail_log(self, n_bytes: int = 8192) -> str:
        tail_lines = max(50, n_bytes // 80)
        return lifecycle.logs_crawl4ai(self._options.container_name, tail_lines)

    def public_host(self) -> str:
        if self._options.public_host:
            return self._options.public_host
        try:
            return socket.gethostname()
        except OSError:
            return "localhost"

    def start(self) -> StartResult:
        volumes = {"/app/data": str(self._data_path)}
        env = {"PUID": str(self._puid), "PGID": str(self._pgid)}

        if self._options.jwt_enabled:
            env["CRAWL4AI_JWT_ENABLED"] = "true"
        else:
            env["CRAWL4AI_API_TOKEN"] = self._resolve_or_generate_api_token()

        return lifecycle.start_crawl4ai(
            image=self.image_ref,
            image_present=self.is_available(),
            container_name=self._options.container_name,
            listen_host=self._options.listen_host,
            listen_port=self._options.listen_port,
            volumes=volumes,
            env=env,
            restart_policy=self._options.restart_policy,
            hostname=self._options.container_name,
            shm_size=self._options.shm_size,
            extra_args=self._options.extra_args,
        )

    def _resolve_or_generate_api_token(self) -> str:
        """Return the ``CRAWL4AI_API_TOKEN`` value to pass to the container.

        Precedence: an explicit ``api_token`` option always wins — the file is
        neither read nor written in that case, so a stale on-disk token can't
        override the user's settings. Otherwise read ``<state_dir>/api_token``
        if present; otherwise generate 256 bits of entropy, persist atomically
        with mode ``0o600`` (only the host user running the worker can read
        it), and return it.

        Returns a non-empty string.
        """
        existing = self.api_token()
        if existing:
            return existing

        token = secrets.token_hex(32)  # 64 hex chars, 256 bits.
        path = self._api_token_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        with tmp.open("w") as f:
            f.write(token)
        tmp.chmod(0o600)
        os.replace(tmp, path)
        return token

    def api_token(self) -> str | None:
        """The ``CRAWL4AI_API_TOKEN`` currently in use, or None.

        Reads from options / disk. Does NOT generate. Returns None when
        ``jwt_enabled=True`` (token is irrelevant in that mode) or when no
        option/file has a value (the service has never been started).

        Surfaced for the UI's copy-to-clipboard affordance.
        """
        if self._options.jwt_enabled:
            return None
        if self._options.api_token:
            return self._options.api_token
        if self._api_token_path.is_file():
            existing = self._api_token_path.read_text().strip()
            if existing:
                return existing
        return None

    def stop(self) -> StopResult:
        return lifecycle.stop_crawl4ai(self._options.container_name)

    def status(self) -> ServiceStatus:
        return lifecycle.status_crawl4ai(
            self._options.container_name,
            self._options.listen_host,
            self._options.listen_port,
        )

    def wait_ready(self, timeout_s: float) -> bool:
        return lifecycle.wait_ready_crawl4ai(
            self._options.listen_host,
            self._options.listen_port,
            timeout_s,
        )

    # --- install axis -----------------------------------------------------

    def installs(self) -> list[ServiceInstall]:
        return [self._install]

    def primary_installable(self) -> ServiceInstall | None:
        return self._install

    def uninstall_installable(self, name: str, *, version: str | None = None) -> None:
        """Remove an installable's installed version. Refuses if the service is running."""
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

    # --- UI ---------------------------------------------------------------

    @property
    def ui_pages(self) -> list[UiPage]:
        ui_dir = Path(__file__).parent / "ui"
        return [
            UiPage(
                "Status",
                ":material/monitor:",
                ui_dir / "status.py",
                url_path="crawl4ai_status",
            ),
            UiPage(
                "Image",
                ":material/inventory_2:",
                ui_dir / "image.py",
                url_path="crawl4ai_image",
            ),
        ]


__all__ = ["Crawl4AiService"]
