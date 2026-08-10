"""Llama-swap inference service."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ...settings import LlamaSwapServiceSettings
from .._base import (
    InferenceService,
    ServiceCapabilities,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
)
from . import lifecycle
from .config import build_config, write_config
from .recipes import Recipes

if TYPE_CHECKING:
    from ...facade import GenesisWorker
    from ...models import Catalog


class LlamaSwapService(InferenceService):
    """Inference service for llama-swap.

    Implements the full :class:`InferenceService` Protocol (lifecycle
    and introspection) plus three service-specific methods the facade
    reaches via ``worker.service("llama_swap")``:

    - :meth:`regenerate_config` — rebuild ``config.yaml`` from catalog +
      recipes + overrides.
    - :meth:`list_recipes` — return parsed recipe entries for UI display.
    - :meth:`export_for_agent` — emit the pi-agent ``models.json``-shaped
      dict (lands in spec-002 chunk 2 with :mod:`agent_export`).

    Construction is by :class:`ServiceRegistry`; the facade injects
    itself via :meth:`bind_worker` after registry construction so the
    service can reach the catalog service for
    :meth:`regenerate_config` without circular imports at module load.
    """

    name = "llama_swap"
    display_name = "llama-swap"

    def __init__(self, settings: LlamaSwapServiceSettings | None = None) -> None:
        # Services without a settings slice get a default. This keeps
        # construction symmetric with sources — the framework always
        # provides a real object.
        self._settings = settings if settings is not None else LlamaSwapServiceSettings()
        # Injected by the facade after registry construction. Lifecycle
        # methods don't need it; service-specific methods (``regenerate_config``)
        # do. Local imports inside those methods break the
        # facade → registry → service → facade cycle.
        self._worker: GenesisWorker | None = None

    def bind_worker(self, worker: GenesisWorker) -> None:
        """Facade injects itself here after construction.

        Splits the construction cycle: the registry builds services,
        then the facade wires itself up. Services don't import the
        facade at module level; the runtime type is reached via this
        setter and via lazy imports in the methods that need it.
        """
        self._worker = worker

    # --- Read-only methods --------------------------------------------------

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
            can_export_for_agent=True,
            can_serve_llm=True,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=False,
        )

    def resource_estimate(self) -> ServiceResourceEstimate:
        """Placeholder advisory budget for the dashboard tile.

        Spec-002 placeholder values, retained for v1. Per-model running
        state isn't tracked yet; the dashboard's resource tile labels
        these numbers as ``~est`` and skips rendering when the typical
        value is zero. Real measurement lands with per-model loaded-
        state tracking (post-v1).
        """
        return ServiceResourceEstimate(
            vram_bytes_typical=5_000_000_000,
            vram_bytes_min=2_000_000_000,
            cpu_cores_recommended=4,
        )

    # --- Lifecycle methods --------------------------------------------------

    def is_running(self) -> bool:
        """True iff the configured tmux session exists."""
        return lifecycle.is_running(self._settings.session_name)

    def runtime_endpoint(self) -> str | None:
        """``http://{listen_addr}/v1`` when the service is running; else None.

        Returns None when the session is absent so callers can show a
        "not running" indicator without probing themselves.
        """
        if not self.is_running():
            return None
        return f"http://{self._settings.listen_addr}/v1"

    def start(self) -> StartResult:
        """Spawn a fresh llama-swap tmux session and wait for /v1/models."""
        s = self._settings
        return lifecycle.start_swap(
            config=self.config_path(),
            listen_addr=s.listen_addr,
            session_name=s.session_name,
            log_file=s.log_file or (s.log_dir / "llama-swap.log"),
            health_timeout_s=s.health_timeout_s,
        )

    def stop(self) -> StopResult:
        """Kill the configured tmux session. Idempotent."""
        return lifecycle.stop_swap(self._settings.session_name)

    def status(self) -> ServiceStatus:
        """Coarse status: session presence + /v1/models probe."""
        s = self._settings
        return lifecycle.status(s.session_name, s.listen_addr)

    def wait_ready(self, timeout_s: float) -> bool:
        """Block (poll) until /v1/models returns 200 or timeout elapses."""
        return lifecycle.wait_ready(self._settings.listen_addr, timeout_s)

    # --- Path resolution ---------------------------------------------------

    def config_path(self) -> Path:
        """Resolve ``config.yaml`` from settings → repo-root → XDG default.

        Resolution order:
        1. explicit ``settings.config_path``
        2. ``<repo_root>/config.yaml`` if it exists
        3. ``<config_dir>/services/llama-swap/config.yaml`` (XDG default)
        """
        s = self._settings
        if s.config_path is not None:
            return s.config_path
        repo_cfg = s.repo_root / "config.yaml"
        if repo_cfg.is_file():
            return repo_cfg
        return s.config_dir / "services" / "llama-swap" / "config.yaml"

    def recipes_path(self) -> Path:
        """Resolve ``recipes.yaml`` from settings → repo-root → XDG default."""
        s = self._settings
        if s.recipes_path is not None:
            return s.recipes_path
        repo_recipes = s.repo_root / "recipes.yaml"
        if repo_recipes.is_file():
            return repo_recipes
        return s.config_dir / "services" / "llama-swap" / "recipes.yaml"

    def overrides_path(self) -> Path:
        """``overrides.yaml`` lives next to ``config.yaml``.

        It's a per-user state file, not part of the module's shipped
        data; placing it next to ``config.yaml`` keeps both user-mutable
        files in one location.
        """
        return self.config_path().parent / "overrides.yaml"

    # --- Service-specific methods ------------------------------------------

    def regenerate_config(self, *, catalog: Catalog | None = None) -> bool:
        """Rebuild ``config.yaml`` from catalog + recipes + overrides.

        Returns True iff the on-disk file changed. The facade passes a
        freshly-scanned catalog; this method never rescans on its own
        (so the Streamlit config-editor page can drive the workflow
        explicitly via ``worker.rescan_catalog()`` + ``regenerate``).
        """
        if self._worker is None:
            raise RuntimeError(
                "LlamaSwapService.regenerate_config requires the facade; "
                "call bind_worker(worker) first."
            )
        from .overrides import OverridesStore

        cat = catalog if catalog is not None else self._worker.rescan_catalog()
        recipes = Recipes.load(self.recipes_path())
        overrides = OverridesStore(self.overrides_path()).load()
        entries = build_config(cat, recipes, overrides=overrides)
        return write_config(
            self.config_path(),
            entries,
            root=cat.root,
            generated_at=cat.generated_at,
        )

    def list_recipes(self) -> Recipes:
        """Return parsed recipes for the UI / CLI to render."""
        return Recipes.load(self.recipes_path())

    def export_for_agent(self, *, base_url: str | None = None) -> dict:
        """Build the pi-agent ``models.json``-shaped dict from ``config.yaml``.

        Implementation lands in spec-002 chunk 2 with :mod:`agent_export`.
        For now raises so callers don't silently get empty output.
        """
        raise NotImplementedError(
            "LlamaSwapService.export_for_agent lands in spec-002 chunk 2 (agent_export.py)"
        )

    # --- config.yaml timestamp helpers -------------------------------------

    def last_generated_at(self) -> str | None:
        """Read the timestamp embedded in ``config.yaml`` when it was last written.

        Returns None if the file is missing or the field is absent
        (older files written before spec-002 lack it).
        """
        from .config import read_generated_at

        return read_generated_at(self.config_path())


__all__ = ["LlamaSwapService"]