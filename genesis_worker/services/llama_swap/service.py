"""Llama-swap inference service."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from ...contracts import (
    Catalog,
    ComputeDevice,
    HostInfo,
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
from ...utils.acquire import fetch_latest_release
from ...utils.installables import (
    InstallableRecord,
    InstallDirs,
    ReleaseCandidate,
    build_installable,
    merge_records,
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
from .installables import (
    candidate_assets,
    load_seed_records,
    parse_repo_url,
)
from .options import LlamaSwapOptions
from .overrides import OverridesStore
from .recipes import BUNDLED_RECIPES_PATH, Recipe, Recipes, RecipesOverlayStore, RecipesStore


class LlamaSwapService(InferenceService):
    """Inference service for llama-swap."""

    name = "llama_swap"
    display_name = "llama-swap"

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__(ctx)
        self._seeds = load_seed_records()
        self._installable_records_error: str | None = None
        try:
            opts = LlamaSwapOptions(**ctx.options)
        except ValidationError as exc:
            if not any(error.get("loc", ("",))[0] == "installables" for error in exc.errors()):
                raise
            clean_options = {
                key: value for key, value in ctx.options.items() if key != "installables"
            }
            opts = LlamaSwapOptions(**clean_options)
            self._installable_records_error = str(exc)
        self._options = opts

        self._config_path = opts.config_path or ctx.data_dir / "config.yaml"
        self._bundled_recipes_path = BUNDLED_RECIPES_PATH
        self._override_recipes_path = opts.recipes_path or self._config_path.parent / "recipes.yaml"
        self._overrides_path = self._config_path.parent / "overrides.yaml"
        self._log_file = opts.log_file or ctx.log_dir / "llama-swap.log"

        self._recipes = RecipesStore([self._bundled_recipes_path, self._override_recipes_path])
        self._recipes_overlay = RecipesOverlayStore(self._override_recipes_path)
        self._overrides = OverridesStore(self._overrides_path)

        self._user_records: list[InstallableRecord] = []
        self._records = merge_records(self._seeds, self._user_records)
        self._installables: dict[str, ServiceInstall] = {}
        try:
            self.set_installable_records(list(opts.installables))
        except ValueError as exc:
            self._installable_records_error = str(exc)
            self._user_records = []
            self._options.installables = []
            self._records = merge_records(self._seeds, self._user_records)
            self._rebuild_installables()
        self._github_token = ctx.secrets.get("github_token")

    def is_available(self) -> bool:
        primary = self.primary_installable()
        return primary is not None and primary.binary_path() is not None

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
        return list(self._installables.values())

    def primary_installable(self) -> ServiceInstall | None:
        """The installable marked as primary, normally the llama-swap proxy."""
        for record in self._records:
            if record.enabled and record.primary:
                return self._installables.get(record.name)
        return None

    def _rebuild_installables(self) -> None:
        dirs = InstallDirs(
            data_dir=self._ctx.data_dir,
            cache_dir=self._ctx.cache_dir,
            state_dir=self._ctx.state_dir,
        )
        self._installables = {
            record.name: build_installable(record, dirs, secrets=self._ctx.secrets)
            for record in self._records
            if record.enabled
        }

    def installable_records(self) -> list[InstallableRecord]:
        """All merged records, including disabled overrides."""
        return list(self._records)

    def user_installable_records(self) -> list[InstallableRecord]:
        """The user-owned records persisted in the service sidecar."""
        return list(self._user_records)

    def seed_installable_records(self) -> list[InstallableRecord]:
        """The bundled records beneath the user's overrides."""
        return list(self._seeds)

    @property
    def installable_records_error(self) -> str | None:
        return self._installable_records_error

    def set_installable_records(self, user_records: list[InstallableRecord]) -> None:
        """Replace the in-memory user records and rebuild the installables."""
        names = [record.name for record in user_records]
        if len(names) != len(set(names)):
            raise ValueError("installable record names must be unique")
        merged = merge_records(self._seeds, user_records)
        variants = [
            record.variant
            for record in merged
            if record.kind == "github_release" and record.variant
        ]
        if "auto" in variants:
            raise ValueError("installable record variant key 'auto' is reserved")
        if len(variants) != len(set(variants)):
            raise ValueError("installable record variant keys must be unique")
        self._user_records = list(user_records)
        self._options.installables = list(user_records)
        self._records = merged
        self._rebuild_installables()

    def disable_installable(self, name: str) -> None:
        """Uninstall and hide a record, leaving a disabled user override."""
        record = next((item for item in self._records if item.name == name), None)
        if record is None:
            raise KeyError(f"unknown installable {name!r}")
        installable = self._installables.get(name)
        try:
            active_binary = self.effective_llama_server_binary()
        except RuntimeError:
            active_binary = None
        if installable is not None and active_binary is not None:
            binary = installable.binary_path()
            if binary is not None and str(binary) == active_binary:
                raise RuntimeError(
                    f"cannot disable {name!r} while its binary is the active llama-server — "
                    "select another variant first"
                )
        if installable is not None:
            self.uninstall_installable(name)
        disabled = record.model_copy(update={"enabled": False})
        overrides = {item.name: item for item in self._user_records}
        overrides[name] = disabled
        self.set_installable_records(list(overrides.values()))

    def list_release_candidates(self, repo_url: str) -> ReleaseCandidate:
        """Return the latest release's Linux x64 archive assets from GitHub metadata."""
        owner, repo = parse_repo_url(repo_url)
        release = fetch_latest_release(owner, repo, auth_token=self._github_token)
        tag = str(release.get("tag_name", ""))
        raw_assets = release.get("assets", [])
        assets = raw_assets if isinstance(raw_assets, list) else []
        candidates = candidate_assets(assets, tag)
        if not candidates:
            raise ValueError(
                f"latest release of {owner}/{repo} has no usable Linux x64 archive asset"
            )
        return ReleaseCandidate(repo=f"{owner}/{repo}", tag=tag, assets=tuple(candidates))

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

    def variant_keys(self) -> list[str]:
        """Variant keys exposed by the currently enabled records."""
        return [
            record.variant
            for record in self._records
            if (record.enabled and record.kind == "github_release" and record.variant is not None)
        ]

    def set_llama_server_variant(self, variant: str | None) -> None:
        """UI write path. ``None`` reverts to legacy fallback."""
        if variant not in (None, "auto", *self.variant_keys()):
            raise ValueError(
                f"unknown variant {variant!r}; expected auto, a record variant, or None"
            )
        self._options.llama_server_variant = variant

    # --- default GPU -------------------------------------------------------
    # Per-machine override of the variant auto-resolve. When set, the
    # matching variant binary is required and the service fails loud at
    # config-regen time if the binary is missing (ADR-039 Q3).

    @property
    def default_device(self) -> ComputeDevice | None:
        return self._options.default_device

    def set_default_device(self, device: ComputeDevice | None) -> None:
        """UI write path. ``None`` reverts to the smart auto-pick.

        When ``device`` is set and is a :class:`GpuDevice`, it must
        appear in ``self._ctx.host_info.hardware.devices`` — stale
        entries (from a previous worker run when the device was
        hot-plugged out) are rejected up front so the UI cannot
        persist a non-existent device. ``CpuDevice`` always passes
        (CPU is always available).
        """
        from ...contracts import GpuDevice

        if isinstance(device, GpuDevice) and device not in self._ctx.host_info.hardware.devices:
            raise ValueError(f"GPU {device!r} not in host_info.hardware.devices — stale snapshot?")
        self._options.default_device = device

    @property
    def effective_default_device(self) -> ComputeDevice:
        """The default device used everywhere downstream.

        Returns the persisted ``default_device`` if set, else the smart
        auto-pick from the host snapshot (NVIDIA 0 → AMD 0 → Intel 0 →
        CPU). Always returns a concrete device on a real host.
        """
        return self._options.default_device or smart_default_compute_device(
            self._ctx.host_info.hardware.devices
        )

    @property
    def host_info(self) -> HostInfo:
        """Read-through to the framework-level host snapshot.

        Exposed so UI pages can populate device dropdowns without
        reaching into the private context.
        """
        return self._ctx.host_info

    # --- always-injected cmd flags ---------------------------------------
    # The set of flags the config generator appends to every llama-server
    # cmd (kv-unified / jinja / flash-attn / checkpoint + prompt-cache
    # sizing / port + host). ``None`` means "use the framework defaults";
    # an explicit list replaces them wholesale. Same ADR-041 split as
    # comfyui's ``extra_args``: the service only updates in-memory state,
    # the UI persists through ``worker.write_service_overrides``.

    @property
    def hardcoded_flags(self) -> list[str] | None:
        return self._options.hardcoded_flags

    def set_hardcoded_flags(self, flags: list[str] | None) -> None:
        """Update ``hardcoded_flags`` in memory.

        Persistence is the UI's job: the caller writes the value to the
        per-service JSON sidecar via ``worker.write_service_overrides``.
        The in-memory update means the next config regen picks up the new
        flags without a worker restart; the sidecar write means a worker
        restart also picks them up via ``Settings.options_for``.

        Empty input normalizes to ``None`` (revert to defaults) so the
        sidecar never carries a redundant empty list.
        """
        cleaned = [f.strip() for f in (flags or []) if f and f.strip()]
        self._options.hardcoded_flags = cleaned or None  # type: ignore[assignment]

    def _default_llama_server_binary(self) -> str | None:
        """Resolve the configured variant to an installed binary path."""
        variant = self.llama_server_variant
        if variant is None:
            return None
        if variant == "auto":
            return self._auto_resolve()
        record = self._record_for_variant(variant)
        if record is None:
            return None
        installable = self._installables.get(record.name)
        if installable is None:
            return None
        binary = installable.binary_path()
        return str(binary) if binary is not None else None

    def _record_for_variant(self, variant: str) -> InstallableRecord | None:
        return next(
            (
                record
                for record in self._records
                if (
                    record.enabled and record.kind == "github_release" and record.variant == variant
                )
            ),
            None,
        )

    def _auto_resolve(self) -> str | None:
        """Force the installable whose backend matches the selected device."""
        backend = self.effective_default_device.variant
        for record in self._records:
            if not record.enabled or record.backend != backend:
                continue
            installable = self._installables.get(record.name)
            if installable is None:
                continue
            binary = installable.binary_path()
            if binary is not None:
                return str(binary)
        raise RuntimeError(
            f"default device requires llama-server-{backend} but it is not "
            "installed — install it via the Binaries page"
        )

    def _variant_binary(self, name: str) -> str | None:
        """Look up an installed variant by its installable name."""
        for installable in self.installs():
            if installable.name == name:
                bp = installable.binary_path()
                if bp is not None:
                    return str(bp)
        return None

    def _backend_for_binary_path(self, binary_path: str | None) -> str | None:
        if binary_path is None:
            return None
        for record in self._records:
            if not record.enabled:
                continue
            installable = self._installables.get(record.name)
            if installable is None:
                continue
            binary = installable.binary_path()
            if binary is not None and str(binary) == binary_path:
                return record.backend
        return None

    def _build_options(self) -> BuildOptions:
        """Build :class:`BuildOptions` with ``default_binary`` re-resolved.

        Re-resolved on every call so newly installed variants are picked
        up on the next config regen without restarting the worker.

        ``binary_variant`` is the resolved variant name when a framework-
        managed binary is in play (``"cuda" / "vulkan" / "cpu"``). It is
        ``None`` when the legacy ``default_binary_rel`` fallback is the
        resolved path, so the cmd renderer does not emit any env-var
        prefix for legacy binaries (we do not know what backend they
        use).
        """
        variant = self.llama_server_variant
        resolved_variant: str | None
        if variant is None:
            resolved_variant = None
        elif variant == "auto":
            binary_path = self._default_llama_server_binary()
            resolved_variant = self._backend_for_binary_path(binary_path)
        else:
            record = self._record_for_variant(variant)
            if record is None:
                raise RuntimeError(f"no llama-server binary for variant {variant!r}")
            resolved_variant = record.backend
        return BuildOptions(
            repo_root=self._ctx.repo_root,
            kv_quant_over=self._options.kv_quant_over_bytes,
            mmproj_offload_over=self._options.mmproj_offload_over_bytes,
            default_binary=self._default_llama_server_binary(),
            default_binary_rel=self._options.default_binary_rel,
            binary_variant=resolved_variant,
            service_default_device=self.effective_default_device,
            hardcoded_flags=(
                tuple(self._options.hardcoded_flags)
                if self._options.hardcoded_flags is not None
                else None
            ),
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
        return lifecycle.is_running(self._options.session_name, self._options.listen_addr)

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
        primary = self.primary_installable()
        binary = primary.binary_path() if primary is not None else None
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


def smart_default_compute_device(
    devices: tuple[ComputeDevice, ...],
) -> ComputeDevice:
    """Deterministic framework-side default device.

    Priority: NVIDIA index 0 if any NVIDIA is present, else AMD index
    0, else Intel index 0, else :class:`CpuDevice` (CPU is the
    universal fallback — always available, never "detected" by the
    collector). The collector enumerates devices in vendor order so
    ``devices[0]`` of the first matching vendor is index 0 by
    construction.
    """
    from ...contracts import CpuDevice, GpuDevice

    for vendor in ("nvidia", "amd", "intel"):
        for d in devices:
            if isinstance(d, GpuDevice) and d.vendor == vendor:
                return d
    return CpuDevice()


__all__ = ["LlamaSwapService"]
