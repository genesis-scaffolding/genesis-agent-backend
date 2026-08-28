"""ComfyUI inference service. Stub — fleshed out in plan-025 sub-phase 3.3."""

from __future__ import annotations

from ...contracts import (
    InferenceService,
    ServiceCapabilities,
    ServiceContext,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
)
from .options import ComfyUiOptions


class ComfyUiService(InferenceService):
    """Stub — real implementation lands in plan-025 sub-phase 3.3."""

    name = "comfyui"
    display_name = "ComfyUI"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        self._options = ComfyUiOptions(**ctx.options)

    def is_available(self) -> bool:
        return False

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            can_generate_config=False,
            can_export_for_agent=False,
            can_serve_llm=False,
            can_serve_image=True,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        )

    def resource_estimate(self) -> ServiceResourceEstimate:
        return ServiceResourceEstimate(
            vram_bytes_typical=12_000_000_000,
            vram_bytes_min=6_000_000_000,
            cpu_cores_recommended=4,
        )

    def is_running(self) -> bool:
        return False

    def runtime_endpoint(self) -> str | None:
        return None

    def web_ui_endpoint(self) -> str | None:
        return None

    def start(self) -> StartResult:
        return StartResult(ok=False, message="not yet implemented")

    def stop(self) -> StopResult:
        return StopResult(ok=False, message="not yet implemented")

    def status(self) -> ServiceStatus:
        return ServiceStatus(state=ServiceStatus.state.__class__.STOPPED)

    def wait_ready(self, timeout_s: float) -> bool:
        return False


__all__ = ["ComfyUiService"]
