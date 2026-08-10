"""Llama-swap inference service."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ...contracts import (
    Catalog,
    InferenceService,
    ServiceCapabilities,
    ServiceContext,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
)
from . import lifecycle
from .export_pi_config import build_provider, write_models_json
from .generate_config import BuildOptions, build_config, read_generated_at, write_config
from .options import LlamaSwapOptions
from .overrides import OverridesStore
from .recipes import BUNDLED_RECIPES_PATH, Recipes, RecipesStore

PI_AGENT_DIR = Path.home() / ".pi" / "agent"


class LlamaSwapService(InferenceService):
    """Inference service for llama-swap."""

    name = "llama_swap"
    display_name = "llama-swap"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        opts = LlamaSwapOptions(**ctx.options)
        self._options = opts

        self._config_path = opts.config_path or ctx.data_dir / "config.yaml"
        self._recipes_path = opts.recipes_path or BUNDLED_RECIPES_PATH
        self._overrides_path = self._config_path.parent / "overrides.yaml"
        self._log_file = opts.log_file or ctx.log_dir / "llama-swap.log"

        self._recipes = RecipesStore(self._recipes_path)
        self._overrides = OverridesStore(self._overrides_path)
        self._build_options = BuildOptions(
            repo_root=ctx.repo_root,
            kv_quant_over=opts.kv_quant_over_bytes,
            mmproj_offload_over=opts.mmproj_offload_over_bytes,
            default_binary_rel=opts.default_binary_rel,
        )

    def is_available(self) -> bool:
        return shutil.which("llama-swap") is not None

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            can_generate_config=True,
            can_export_for_agent=True,
            can_serve_llm=True,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=False,
        )

    def resource_estimate(self) -> ServiceResourceEstimate:
        return ServiceResourceEstimate(
            vram_bytes_typical=5_000_000_000,
            vram_bytes_min=2_000_000_000,
            cpu_cores_recommended=4,
        )

    # --- Lifecycle ---------------------------------------------------------

    def is_running(self) -> bool:
        return lifecycle.is_running(self._options.session_name)

    def runtime_endpoint(self) -> str | None:
        if not self.is_running():
            return None
        return f"http://{self._options.listen_addr}/v1"

    def start(self) -> StartResult:
        return lifecycle.start_swap(
            config=self._config_path,
            listen_addr=self._options.listen_addr,
            session_name=self._options.session_name,
            log_file=self._log_file,
            health_timeout_s=self._options.health_timeout_s,
        )

    def stop(self) -> StopResult:
        return lifecycle.stop_swap(self._options.session_name)

    def status(self) -> ServiceStatus:
        return lifecycle.status(self._options.session_name, self._options.listen_addr)

    def wait_ready(self, timeout_s: float) -> bool:
        return lifecycle.wait_ready(self._options.listen_addr, timeout_s)

    # --- Config generation -------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def overrides_path(self) -> Path:
        return self._overrides_path

    @property
    def recipes_path(self) -> Path:
        return self._recipes_path

    def list_recipes(self) -> Recipes:
        return self._recipes.load()

    def regenerate_config(self, catalog: Catalog) -> bool:
        entries = build_config(
            catalog,
            self._recipes.load(),
            overrides=self._overrides.load(),
            options=self._build_options,
        )
        return write_config(
            self._config_path,
            entries,
            root=catalog.root,
            generated_at=catalog.generated_at,
        )

    def last_generated_at(self) -> str | None:
        return read_generated_at(self._config_path)

    # --- pi-agent export ---------------------------------------------------

    def export_for_agent(self, *, base_url: str | None = None) -> dict:
        return build_provider(self._config_path, base_url=base_url)

    def write_agent_config(self, target: Path, *, base_url: str | None = None) -> bool:
        return write_models_json(target, self.export_for_agent(base_url=base_url))

    def agent_config_target(self) -> Path:
        base = Path(os.environ.get("PI_INSTALL_DIR") or PI_AGENT_DIR)
        return base / "models.json"


__all__ = ["LlamaSwapService"]
