"""cptr (Open WebUI Computer) inference service plugin — subclass of UvService."""

from __future__ import annotations

import os
import site
from pathlib import Path

from ...contracts import ServiceCapabilities, ServiceCategory, ServiceContext
from ...utils.services import UvService, UvServiceConfig
from .options import CptrOptions

_PI_TIMEOUT_OLD = "timeout=120"
_PI_TIMEOUT_NEW = "timeout=900"


def _find_pi_file() -> Path | None:
    """Find ``cptr/utils/agents/pi.py`` in site-packages or the uv-tool isolated env.

    Searches the current Python's site-packages (normal installs) and the
    uv-tool isolated env (``~/.local/share/uv/tools/cptr/lib``) for the
    cptr package. Returns the first writable match.
    """
    candidates: list[Path] = []
    for sp in site.getsitepackages():
        p = Path(sp) / "cptr" / "utils" / "agents" / "pi.py"
        if p.is_file():
            candidates.append(p)
    uv_tool_dir = Path.home() / ".local" / "share" / "uv" / "tools" / "cptr" / "lib"
    if uv_tool_dir.is_dir():
        for child in uv_tool_dir.iterdir():
            if child.is_dir() and child.name.startswith("python"):
                p = child / "site-packages" / "cptr" / "utils" / "agents" / "pi.py"
                if p.is_file():
                    candidates.append(p)
    for p in candidates:
        if os.access(p, os.W_OK):
            return p
    return candidates[0] if candidates else None


def patch_pi_timeout() -> bool:
    """Replace ``timeout=120`` with ``timeout=900`` on the pi-agent event-wait call.

    Idempotent: returns False if the file is absent or already patched.
    The 900s window lets the cptr HTTP/event transport outlive the
    120s default, so local GPU inference workloads don't trip the
    pi-agent timeout before the response arrives.
    """
    pi_file = _find_pi_file()
    if pi_file is None:
        return False
    text = pi_file.read_text()
    if _PI_TIMEOUT_OLD not in text:
        return False
    pi_file.write_text(text.replace(_PI_TIMEOUT_OLD, _PI_TIMEOUT_NEW))
    return True


class CptrService(UvService):
    """Inference-service-shaped plugin for Open WebUI Computer.

    Subclasses :class:`UvService`. The only Python custom code we
    retain is the post-install pi-agent timeout patch — everything
    else (lifecycle, installable, install path) is inherited.
    """

    name = "cptr"
    display_name = "Open WebUI Computer"

    def __init__(self, ctx: ServiceContext) -> None:
        opts = CptrOptions(**ctx.options)
        config = UvServiceConfig(
            name="cptr",
            display_name=self.display_name,
            description="Open WebUI automation",
            category=ServiceCategory.CHAT,
            capabilities=ServiceCapabilities(
                can_generate_config=False,
                can_export_for_agent=False,
                can_serve_llm=False,
                can_serve_image=False,
                can_train_models=False,
                has_web_ui=True,
                can_install=True,
            ),
            resource_estimate=self._resource_estimate(),
            options_model=CptrOptions,
            package_name="cptr",
            binary_name="cptr",
            command=["run", "--host", opts.listen_host, "--port", str(opts.listen_port)],
            listen_host=opts.listen_host,
            listen_port=opts.listen_port,
            health_probe_path="/",
            health_timeout_s=opts.health_timeout_s,
            session_name=opts.session_name,
            graceful_stop_timeout_s=10.0,
            log_filename=opts.log_file.name if opts.log_file else "cptr.log",
        )
        super().__init__(ctx, config=config)
        # Override the inherited log_file resolution: respect explicit ``log_file`` option.
        if opts.log_file is not None:
            self._explicit_log_file = opts.log_file

    @property
    def log_file(self) -> Path:
        """``opts.log_file`` wins; otherwise the default under ``ctx.log_dir``."""
        explicit = getattr(self, "_explicit_log_file", None)
        if explicit is not None:
            return explicit
        return super().log_file

    @property
    def description(self) -> str:
        return "Open WebUI automation"

    @property
    def category(self) -> ServiceCategory:
        return ServiceCategory.CHAT

    @staticmethod
    def _resource_estimate():
        from ...contracts import ServiceResourceEstimate

        return ServiceResourceEstimate(
            vram_bytes_typical=0,
            vram_bytes_min=0,
            cpu_cores_recommended=2,
        )

    def _post_install(self) -> None:
        """Apply cptr's runtime pi-agent timeout patch.

        Idempotent — returns False if the file is absent or already
        patched. Runs at every ``start()`` so the patch survives
        user-initiated reinstalls. Failures are swallowed: a missing
        pi.py on the host (e.g. pre-install) must not block ``start()``.
        """
        try:
            patch_pi_timeout()
        except Exception:  # noqa: BLE001
            return


__all__ = ["CptrService", "patch_pi_timeout"]
