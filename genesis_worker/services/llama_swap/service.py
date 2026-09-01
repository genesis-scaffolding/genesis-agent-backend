"""Llama-swap inference service."""

from __future__ import annotations

import os
from pathlib import Path

from ...contracts import (
    Catalog,
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
from .export_pi_config import build_provider_from_configs, write_models_json
from .generate_config import (
    BuildOptions,
    EvaluatedConfig,
    build_config,
    evaluate_all,
    read_generated_at,
    write_config,
)
from .installs import LlamaServerCPU, LlamaServerCUDA, LlamaServerVulkan, LlamaSwapBinary
from .options import LlamaSwapOptions
from .overrides import OverridesStore
from .recipes import BUNDLED_RECIPES_PATH, Recipe, Recipes, RecipesOverlayStore, RecipesStore


class LlamaSwapService(InferenceService):
    """Inference service for llama-swap."""

    name = "llama_swap"
    display_name = "llama-swap"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        opts = LlamaSwapOptions(**ctx.options)
        self._options = opts

        self._config_path = opts.config_path or ctx.data_dir / "config.yaml"
        self._bundled_recipes_path = BUNDLED_RECIPES_PATH
        self._override_recipes_path = opts.recipes_path or self._config_path.parent / "recipes.yaml"
        self._overrides_path = self._config_path.parent / "overrides.yaml"
        self._log_file = opts.log_file or ctx.log_dir / "llama-swap.log"

        self._recipes = RecipesStore([self._bundled_recipes_path, self._override_recipes_path])
        self._recipes_overlay = RecipesOverlayStore(self._override_recipes_path)
        self._overrides = OverridesStore(self._overrides_path)

        self._llama_swap_install = LlamaSwapBinary(
            data_dir=ctx.data_dir,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            secrets=ctx.secrets,
        )
        self._llama_server_cuda_install = LlamaServerCUDA(
            data_dir=ctx.data_dir,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            secrets=ctx.secrets,
        )
        self._llama_server_cpu_install = LlamaServerCPU(
            data_dir=ctx.data_dir,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            secrets=ctx.secrets,
        )
        self._llama_server_vulkan_install = LlamaServerVulkan(
            data_dir=ctx.data_dir,
            cache_dir=ctx.cache_dir,
            state_dir=ctx.state_dir,
            secrets=ctx.secrets,
        )

    def is_available(self) -> bool:
        return self._llama_swap_install.binary_path() is not None

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            can_generate_config=True,
            can_export_for_agent=True,
            can_serve_llm=True,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
            can_install=True,
        )

    @property
    def category(self) -> ServiceCategory:
        return ServiceCategory.LLM

    @property
    def description(self) -> str:
        return "OpenAI-compatible LLM server"

    def installs(self) -> list[ServiceInstall]:
        return [
            self._llama_swap_install,
            self._llama_server_cuda_install,
            self._llama_server_cpu_install,
            self._llama_server_vulkan_install,
        ]

    def primary_installable(self) -> ServiceInstall | None:
        """The llama-swap binary. The llama-server variants are not 'primary' — they
        stay on the Binaries page because the variant pick (CUDA vs CPU vs Vulkan)
        doesn't fit on the dashboard.
        """
        return self._llama_swap_install

    def uninstall_installable(self, name: str, *, version: str | None = None) -> None:
        """Remove an installable's installed version. Refuses if the service is running.

        Without this guard, deleting the on-disk binary while the
        process is running is a silent no-op: the running binary was
        already exec'd into memory, so deletion succeeds, but our
        later ``start()`` call can't find the file. Refusing up front
        surfaces the conflict explicitly.
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

    # --- llama-server variant resolution -----------------------------------
    # The framework manages three llama-server installables (cuda / cpu /
    # vulkan). The variant setting picks which one's binary the config
    # generator uses as the default. ``auto`` runs ``nvidia-smi`` to
    # decide between CUDA and the rest. ``None`` falls back to the legacy
    # ``default_binary_rel`` path.

    @property
    def llama_server_variant(self) -> str | None:
        return self._options.llama_server_variant

    def set_llama_server_variant(self, variant: str | None) -> None:
        """UI write path. ``None`` reverts to legacy fallback."""
        if variant not in ("auto", "cuda", "cpu", "vulkan", None):
            raise ValueError(f"unknown variant {variant!r}; expected auto/cuda/cpu/vulkan or None")
        self._options.llama_server_variant = variant  # type: ignore[assignment]

    def _default_llama_server_binary(self) -> str | None:
        """Resolve the configured variant to an installed binary path."""
        variant = self.llama_server_variant
        if variant is None:
            return None
        if variant == "auto":
            return self._auto_resolve()
        return self._variant_binary(f"llama-server-{variant}")

    def _auto_resolve(self) -> str | None:
        """Priority: NVIDIA + cuda installed → cuda; else vulkan; else cpu.

        Hardware presence comes from the framework-level snapshot
        (``ctx.host_info.hardware``); no per-service nvidia-smi probe
        needed. AMD-only hosts naturally land on vulkan because ROCm
        surfaces through Vulkan — that's the right answer for both
        AMD APUs and discrete Radeon cards.
        """
        if self._ctx.host_info.hardware.nvidia:
            binary = self._variant_binary("llama-server-cuda")
            if binary is not None:
                return binary
        binary = self._variant_binary("llama-server-vulkan")
        if binary is not None:
            return binary
        return self._variant_binary("llama-server-cpu")

    def _variant_binary(self, name: str) -> str | None:
        """Look up an installed variant by its installable name."""
        for installable in self.installs():
            if installable.name == name:
                bp = installable.binary_path()
                if bp is not None:
                    return str(bp)
        return None

    def _build_options(self) -> BuildOptions:
        """Build :class:`BuildOptions` with ``default_binary`` re-resolved.

        Re-resolved on every call so newly installed variants are picked
        up on the next config regen without restarting the worker.
        """
        return BuildOptions(
            repo_root=self._ctx.repo_root,
            kv_quant_over=self._options.kv_quant_over_bytes,
            mmproj_offload_over=self._options.mmproj_offload_over_bytes,
            default_binary=self._default_llama_server_binary(),
            default_binary_rel=self._options.default_binary_rel,
        )

    def is_ready_to_serve(self) -> bool:
        """True iff a llama-server binary is reachable for config generation.

        Checked: the configured variant's binary is installed, or the
        legacy ``default_binary_rel`` resolves to an existing file.
        The Status and Config editor gates the "Regenerate config"
        button on this so the user can't write a config whose cmd
        references a missing binary.
        """
        if self._default_llama_server_binary() is not None:
            return True
        return self._legacy_binary_exists()

    def _legacy_binary_exists(self) -> bool:
        legacy = self._options.default_binary_rel
        if legacy is None:
            return False
        path = Path(legacy)
        if not path.is_absolute():
            path = self._ctx.repo_root / path
        return path.is_file()

    def effective_llama_server_binary(self) -> str | None:
        """Public name for the resolved binary path. The Status page uses
        this to show what the config will pick."""
        return self._default_llama_server_binary()

    def resource_estimate(self) -> ServiceResourceEstimate:
        return ServiceResourceEstimate(
            vram_bytes_typical=5_000_000_000,
            vram_bytes_min=2_000_000_000,
            cpu_cores_recommended=4,
        )

    # --- Lifecycle ---------------------------------------------------------

    def is_running(self) -> bool:
        return lifecycle.is_running(self._options.session_name)

    def tail_log(self, n_bytes: int = 8192) -> str:
        """Return the last ``n_bytes`` of the log file, or "" if missing.

        The lifecycle pipes llama-swap's stdout/stderr into the log
        file via ``tee -a``, so this is the canonical source for the
        process's console output. UI pages that want a live tail wrap
        this in a ``@st.fragment(run_every=...)``.
        """
        if not self._log_file.is_file():
            return ""
        with self._log_file.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            return f.read().decode("utf-8", errors="replace")

    def public_host(self) -> str:
        """Hostname clients should use to reach this service.

        Defaults to ``socket.gethostname()`` so the dashboard, pi-agent
        exports, and web-UI links are reachable from other machines on
        the LAN/VPN — not from ``127.0.0.1`` (which points at the calling
        machine, not the worker) and not from ``0.0.0.0`` (a bind address,
        not a connect address).
        """
        if self._options.public_host:
            return self._options.public_host
        import socket

        try:
            return socket.gethostname()
        except OSError:
            return "localhost"

    def _port(self) -> int:
        # listen_addr is "host:port" — we want the port for URL building.
        try:
            return int(self._options.listen_addr.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return 8080

    def runtime_endpoint(self) -> str | None:
        """OpenAI-compatible API base URL (``/v1``). Used by pi-agent."""
        if not self.is_running():
            return None
        return f"http://{self.public_host()}:{self._port()}/v1"

    def web_ui_endpoint(self) -> str | None:
        """Web UI URL. Used by the dashboard's 'Open Web UI' button."""
        if not self.is_running():
            return None
        return f"http://{self.public_host()}:{self._port()}/"

    def start(self) -> StartResult:
        binary = self._llama_swap_install.binary_path()
        if binary is None:
            return StartResult(ok=False, message="llama-swap binary not installed")
        return lifecycle.start_swap(
            binary=binary,
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
    def recipe_sources(self) -> list[tuple[str, Path]]:
        return [
            ("bundled", self._bundled_recipes_path),
            ("override", self._override_recipes_path),
        ]

    def list_recipes(self) -> Recipes:
        return self._recipes.load()

    def reload_recipes(self) -> Recipes:
        """Re-read bundled + override recipes, refresh the in-memory store."""
        return self._recipes.reload()

    @property
    def recipe_overlay_path(self) -> Path:
        return self._override_recipes_path

    def list_recipe_overrides(self) -> dict[str, Recipe]:
        raw = self._recipes_overlay.load()
        return {name: Recipe(name=name, source="override", **body) for name, body in raw.items()}

    def save_recipe_override(self, name: str, fields: dict) -> None:
        merged = self.list_recipes()
        base = None
        if merged.default and merged.default.name == name:
            base = merged.default
        else:
            base = next((r for r in merged.matchable if r.name == name), None)
        full_body = {}
        if base is not None:
            # Build dict from base recipe excluding source/name
            full_body = base.model_dump(exclude={"source", "name"})
        full_body.update(fields)
        # Validate
        Recipe(name=name, **full_body)
        self._recipes_overlay.update_recipe(name, full_body)
        self.reload_recipes()

    def delete_recipe_override(self, name: str) -> None:
        self._recipes_overlay.delete_recipe(name)
        self.reload_recipes()

    def regenerate_config(self, catalog: Catalog) -> bool:
        entries = build_config(
            catalog,
            self._recipes.load(),
            overrides=self._overrides.load(),
            options=self._build_options(),
        )
        return write_config(
            self._config_path,
            entries,
            root=catalog.root,
            generated_at=catalog.generated_at,
        )

    def last_generated_at(self) -> str | None:
        return read_generated_at(self._config_path)

    def evaluate_model_config(self, catalog: Catalog) -> dict[str, EvaluatedConfig]:
        """Resolve every catalog entry's effective config (structured).

        Walks the catalog with the same recipe + overrides as
        :meth:`regenerate_config`, but returns structured fields instead
        of writing yaml. Used by the config editor UI to render and
        edit.
        """
        return evaluate_all(
            catalog,
            self._recipes.load(),
            overrides=self._overrides.load(),
            options=self._build_options(),
        )

    def list_overrides(self) -> dict[str, dict]:
        return self._overrides.load()

    def save_overrides_for_entry(self, entry_id: str, fields: dict) -> None:
        """Write/clear this entry's override block. ``{}`` clears it.

        Other entries' overrides are preserved.
        """
        entries = self._overrides.load()
        if fields:
            entries[entry_id] = fields
        else:
            entries.pop(entry_id, None)
        self._overrides.save(entries)

    # --- pi-agent export ---------------------------------------------------

    def export_for_agent(self, *, catalog: Catalog, base_url: str | None = None) -> dict:
        """Build the pi-agent ``models.json`` payload from structured configs.

        Runs the same evaluation pipeline that produces ``config.yaml``;
        the export reads ``ctx_size``, ``ctx_min``, mmproj presence, and
        chat-template-kwargs straight off the ``EvaluatedConfig`` rather
        than regexing the rendered cmd.
        """
        return build_provider_from_configs(
            self.evaluate_model_config(catalog),
            base_url=base_url,
        )

    def write_agent_config(
        self, target: Path, *, catalog: Catalog, base_url: str | None = None
    ) -> bool:
        return write_models_json(target, self.export_for_agent(catalog=catalog, base_url=base_url))

    def agent_config_target(self) -> Path:
        base = os.environ.get("PI_INSTALL_DIR")
        return (Path(base) if base else Path.home() / ".pi" / "agent") / "models.json"

    @property
    def ui_pages(self) -> list[UiPage]:
        ui_dir = Path(__file__).parent / "ui"
        return [
            UiPage("Status", ":material/monitor:", ui_dir / "status.py"),
            UiPage("Binaries", ":material/inventory_2:", ui_dir / "binaries.py"),
            UiPage("Config editor", ":material/tune:", ui_dir / "config_editor.py"),
            UiPage("Recipes view", ":material/menu_book:", ui_dir / "recipes_view.py"),
            UiPage("Pi export", ":material/download:", ui_dir / "pi_export.py"),
        ]


__all__ = ["LlamaSwapService"]
