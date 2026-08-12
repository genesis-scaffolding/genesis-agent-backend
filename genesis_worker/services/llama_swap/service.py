"""Llama-swap inference service."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from ...contracts import (
    Catalog,
    InferenceService,
    ServiceCapabilities,
    ServiceContext,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
    UiPage,
)
from . import lifecycle
from .export_pi_config import build_provider, write_models_json
from .generate_config import BuildOptions, build_config, read_generated_at, write_config
from .options import LlamaSwapOptions
from .overrides import OverridesStore
from .parse_cmd import parse_cmd
from .recipes import BUNDLED_RECIPES_PATH, Recipes, RecipesStore


@dataclass(frozen=True)
class ModelConfigEntry:
    """One entry from config.yaml's ``models:`` block, with the cmd pre-parsed."""

    name: str
    cmd: str
    binary: str
    flags: list[tuple[str, str | bool]]
    proxy: str
    ttl: int


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
            has_web_ui=True,
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

    def read_config_models(self) -> dict[str, ModelConfigEntry]:
        """Parse the live config.yaml and return each model's structured entry.

        Returns ``{}`` if config.yaml does not exist. Lets
        ``yaml.YAMLError`` propagate on malformed input so the caller can
        decide how to surface it.
        """
        if not self._config_path.is_file():
            return {}
        raw = yaml.safe_load(self._config_path.read_text())
        models = (raw or {}).get("models") or {}
        out: dict[str, ModelConfigEntry] = {}
        for entry_id, body in models.items():
            cmd = body.get("cmd", "") or ""
            parsed = parse_cmd(cmd)
            out[entry_id] = ModelConfigEntry(
                name=body.get("name", entry_id),
                cmd=cmd,
                binary=parsed.binary,
                flags=parsed.flags,
                proxy=body.get("proxy", ""),
                ttl=body.get("ttl", 0),
            )
        return out

    # --- pi-agent export ---------------------------------------------------

    def export_for_agent(self, *, base_url: str | None = None) -> dict:
        return build_provider(self._config_path, base_url=base_url)

    def write_agent_config(self, target: Path, *, base_url: str | None = None) -> bool:
        return write_models_json(target, self.export_for_agent(base_url=base_url))

    def agent_config_target(self) -> Path:
        base = os.environ.get("PI_INSTALL_DIR")
        return (Path(base) if base else Path.home() / ".pi" / "agent") / "models.json"

    @property
    def ui_pages(self) -> list[UiPage]:
        ui_dir = Path(__file__).parent / "ui"
        return [
            UiPage("Status",        ":material/monitor:",   ui_dir / "status.py"),
            UiPage("Config editor", ":material/tune:",      ui_dir / "config_editor.py"),
            UiPage("Recipes view",  ":material/menu_book:", ui_dir / "recipes_view.py"),
            UiPage("Pi export",     ":material/download:",  ui_dir / "pi_export.py"),
        ]


__all__ = ["LlamaSwapService"]
