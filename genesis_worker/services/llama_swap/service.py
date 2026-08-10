"""Llama-swap inference service.

Wraps the llama-swap process for the worker. This module establishes
the concrete :class:`InferenceService` implementation for llama-swap;
the read-only methods (``is_available``, ``capabilities``) are
implemented now, while the lifecycle methods (``start`` / ``stop`` /
``status`` / ``is_running`` / ``runtime_endpoint`` / ``wait_ready`` /
``resource_estimate``) land in plan-002 with the tmux + curl + psutil
plumbing in :mod:`genesis_worker.services.llama_swap.lifecycle`.

The class is constructed by :class:`~genesis_worker.services._registry.ServiceRegistry`
with the per-service settings slice (``settings.services.llama_swap``).
It does not import ``Settings`` directly — it receives its slice at
construction, mirroring how :class:`~genesis_worker.sources.HuggingFaceSource`
receives ``local_path``.

ADR-003 establishes llama-swap as one of multiple peers (ComfyUI,
AIToolkit, vLLM are future axes). The :class:`InferenceService` Protocol
keeps the dashboard capability-driven; this implementation reports
``can_serve_llm=True`` and ``can_generate_config=True``.
"""

from __future__ import annotations

import shutil

from ...settings import LlamaSwapServiceSettings
from .._base import (
    InferenceService,
    ServiceCapabilities,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
)


class LlamaSwapService(InferenceService):
    """Inference service for llama-swap.

    Implements the read-only surface of :class:`InferenceService` now;
    lifecycle hooks (``start``, ``stop``, ``status``, etc.) raise
    :class:`NotImplementedError` and are filled in by plan-002 with the
    tmux + curl plumbing.

    Construction is by :class:`ServiceRegistry`; this class accepts its
    per-service settings slice as ``settings``.
    """

    name = "llama_swap"
    display_name = "llama-swap"

    def __init__(self, settings: LlamaSwapServiceSettings | None = None) -> None:
        # Services without a settings slice (or with a future one that
        # hasn't landed) get a default. This keeps construction symmetric
        # with sources — the framework always provides a real object.
        self._settings = settings if settings is not None else LlamaSwapServiceSettings()

    # --- Real (read-only) methods -------------------------------------------

    def is_available(self) -> bool:
        """llama-swap binary on PATH and (if configured) its config file exists."""
        if shutil.which("llama-swap") is None:
            return False
        config = self._settings.config_path
        # Available if no config is specified, or if the specified config exists.
        return config is None or config.is_file()

    def capabilities(self) -> ServiceCapabilities:
        """Static capability declaration. Drives the dashboard's tile rendering."""
        return ServiceCapabilities(
            can_generate_config=True,
            can_export_for_agent=True,  # pi-models.json emission (plan-002)
            can_serve_llm=True,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=False,
        )

    # --- Lifecycle methods (plan-002) --------------------------------------

    def resource_estimate(self) -> ServiceResourceEstimate:
        """Compute a resource estimate from the loaded config.

        Plan-002: parses the current ``config.yaml`` to sum weight bytes
        across currently-loaded models, queries psutil / pynvml for
        available VRAM, returns the estimate.
        """
        raise NotImplementedError(
            "LlamaSwapService.resource_estimate lands in plan-002 (psutil + pynvml)"
        )

    def is_running(self) -> bool:
        """Plan-002: ``tmux has-session -t <session_name>``."""
        raise NotImplementedError("LlamaSwapService.is_running lands in plan-002 (tmux)")

    def runtime_endpoint(self) -> str | None:
        """Plan-002: ``http://{listen_addr}/v1`` if running, else None."""
        raise NotImplementedError("LlamaSwapService.runtime_endpoint lands in plan-002")

    def start(self) -> StartResult:
        """Plan-002: tmux launch + curl polling, lifted from ``bin/up``."""
        raise NotImplementedError("LlamaSwapService.start lands in plan-002 (tmux + curl)")

    def stop(self) -> StopResult:
        """Plan-002: ``tmux kill-session -t <session_name>``."""
        raise NotImplementedError("LlamaSwapService.stop lands in plan-002 (tmux)")

    def status(self) -> ServiceStatus:
        """Plan-002: tmux check + /v1/models probe."""
        raise NotImplementedError("LlamaSwapService.status lands in plan-002 (tmux + curl)")

    def wait_ready(self, timeout_s: float) -> bool:
        """Plan-002: poll ``http://{listen_addr}/v1/models`` until 200 or timeout."""
        raise NotImplementedError("LlamaSwapService.wait_ready lands in plan-002 (curl polling)")


__all__ = ["LlamaSwapService"]
