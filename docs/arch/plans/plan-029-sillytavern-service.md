# Plan: Add SillyTavern inference service

Steps for adding a new Docker-container inference service — SillyTavern — modeled on the comfyui service plugin (ADR-025). SillyTavern is an LLM chat UI, so this differs from comfyui in three ways: no host-NVIDIA GPU handling, a config+data mount pair instead of model-weight symlinks, and an arch-agnostic public image.

## Step 1 — `options.py`

`genesis_worker/services/sillytavern/options.py` — `SillyTavernOptions(BaseModel)`:
- Networking: `listen_host="0.0.0.0"`, `listen_port=9090` (host/published), `public_host` (None).
- Image: `image_repo="ghcr.io/sillytavern/sillytavern"`, `image_tag="latest"` (others listed by the registry).
- Identity: `container_name="sillytavern"`, `health_timeout_s=60.0`, `log_file` (None → ctx default).
- Runtime: `restart_policy="unless-stopped"`, `puid`/`pgid` (None → host uid/gid), `extra_args`.
- Mounts: `config_path`/`data_path` (None → `ctx.data_dir/sillytavern/{config,data}`); optional `extensions_path`/`plugins_path`.

No GPU options. No TZ (deferred).

## Step 2 — `install.py`

`genesis_worker/services/sillytavern/install.py` — `SillyTavernImage(ServiceInstall)`, adapted from `ComfyUiImage`:
- Remove arch filtering (`_matches_host_arch`, `_TAG_ARCH_RE`, `_ARCH_SUFFIXES`).
- `name = "sillytavern"`, `source_url` = the ST GitHub Packages container page.
- Keep: 15-min tag cache, `available_versions` (arch-agnostic), selection file, `install` → `DockerPullAcquireSession`, `uninstall` → `docker rmi`.

## Step 3 — `lifecycle.py`

`genesis_worker/services/sillytavern/lifecycle.py`:
- `start_sillytavern` mounts config+data always; extensions/plugins only when set; env `PUID`/`PGID`; maps `{8000/tcp}: (listen_host, listen_port)` (container port 8000 fixed); no `runtime`/`gpu_flags`; forwards `extra_args`.
- `status`/`wait_ready` use `HealthProbe` on `/` against the host port; `is_running`/`logs` via `DockerContainer`.

## Step 4 — `service.py`

`genesis_worker/services/sillytavern/service.py` — `SillyTavernService(InferenceService)`:
- `name="sillytavern"`, `display_name="SillyTavern"`.
- No GPU probe; no symlink applier.
- `capabilities()`: `can_install=True`, `has_web_ui=True`, rest False.
- `web_ui_endpoint` → `http://<public_host>:9090/`; `runtime_endpoint` → None.
- Installs → `[self._install]`.

## Step 5 — `__init__.py` and UI

- `__init__.py` re-exports `SillyTavernOptions`, `SillyTavernService`.
- `ui/status.py` — landing page: ServiceInfo/controls + container info + console tail. No GPU row.
- `ui/image.py` — version picker + Install/Reinstall/Uninstall/Refresh tags. No GPU-disable logic.

## Step 6 — Gates

Run before committing:
- `uv run pytest -q`
- `uv run pyright`
- `uv run ruff check genesis_worker`
- `uv run ruff format --check genesis_worker`

`test_plugin_boundary.py` will re-walk the new package — imports must stay within `contracts`/`utils`.

## Verification summary

- Service auto-discovered from `settings.services.sillytavern`; no framework/settings/registry changes.
- Runs `ghcr.io/sillytavern/sillytavern:<tag>` on host port 9090, config+data mounted under the service data dir.
- No host GPU dependency; no model-weight symlinks.
