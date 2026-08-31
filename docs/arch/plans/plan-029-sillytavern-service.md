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

## Step 3 — `config.py` (Docker host reachability)

`genesis_worker/services/sillytavern/config.py` — seeds `config.yaml` so the Docker-published host is not blocked by SillyTavern's default whitelist.

- **Problem:** ST ships `whitelist: [127.0.0.1]` + `whitelistDockerHosts: true`. On Docker-CE-on-Linux `whitelistDockerHosts` tries to resolve `host.docker.internal` / `gateway.docker.internal` (ENOTFOUND) while the real client IP is never whitelisted. Two client paths hit the container: (a) `localhost` / the health probe arrives NAT'd from the docker bridge gateway (`172.17.0.1`); (b) the docker host itself over its own LAN/hostname IP (e.g. `p3:9090`) reaches the container with that raw source IP — which neither path whitelisted.
- **Fix:** `seed_config(config_path)` runs on every `start()` and **idempotently** enforces the security keys (never clobbers unrelated edits), correcting a config that already exists at ST's defaults too. It sets `whitelistDockerHosts: false` and guarantees `whitelist` contains `127.0.0.1`, the docker bridge gateway(s) (health-check source), and every address this host itself presents via its own interfaces (LAN / hostname / tailscale). Bridge gateways are found via `docker network ls -q` + `docker network inspect <ids>`, filtering by `Driver == "bridge"` (some daemons omit `Type`); falls back to `172.17.0.1` when Docker/network info is unavailable. Keeps whitelist mode on so SSRF `privateAddressWhitelist` stays protected; the set we add is limited to the host itself, never the wider LAN or internet.
- The entrypoint copies `default/config.yaml` only when the file is missing and `npm run init` fills missing keys without overwriting — so our values survive. Verified end-to-end: host curl → HTTP 200, no blocked requests.
- `--whitelist=false` was rejected as a flag alternative: alone it makes the server exit (code 1); combined with `--basicAuthMode` it runs but forces a login on every access.

## Step 4 — `lifecycle.py`

`genesis_worker/services/sillytavern/lifecycle.py`:
- `start_sillytavern` mounts config+data always; extensions/plugins only when set; env `PUID`/`PGID`; maps `{8000/tcp}: (listen_host, listen_port)` (container port 8000 fixed); no `runtime`/`gpu_flags`; forwards `extra_args`.
- `status`/`wait_ready` use `HealthProbe` on `/` against the host port; `is_running`/`logs` via `DockerContainer`.

`service.start()` calls `seed_config(self._config_path)` before `lifecycle.start_sillytavern` so the file is in place before the container boots.

## Step 5 — `service.py`

`genesis_worker/services/sillytavern/service.py` — `SillyTavernService(InferenceService)`:
- `name="sillytavern"`, `display_name="SillyTavern"`.
- No GPU probe; no symlink applier.
- `capabilities()`: `can_install=True`, `has_web_ui=True`, rest False.
- `web_ui_endpoint` → `http://<public_host>:9090/`; `runtime_endpoint` → None.
- Installs → `[self._install]`.

## Step 6 — `__init__.py` and UI

- `__init__.py` re-exports `SillyTavernOptions`, `SillyTavernService`.
- `ui/status.py` — landing page: ServiceInfo/controls + container info + console tail. No GPU row.
- `ui/image.py` — version picker + Install/Reinstall/Uninstall/Refresh tags. No GPU-disable logic.

## Step 7 — Tests + Gates

Run before committing:
- `uv run pytest -q`
- `uv run pyright`
- `uv run ruff check genesis_worker`
- `uv run ruff format --check genesis_worker`

`test_plugin_boundary.py` will re-walk the new package — imports must stay within `contracts`/`utils`.

New `tests/test_sillytavern_config.py` covers `seed_config` and the two helpers: writes / corrects a pre-existing default / preserves other keys / includes detected gateways + host own-addresses / falls back without docker / no-op when already correct / `ip` parsing + no-`ip` fallback.

## Verification summary

- Service auto-discovered from `settings.services.sillytavern`; no framework/settings/registry changes.
- Runs `ghcr.io/sillytavern/sillytavern:<tag>` on host port 9090, config+data mounted under the service data dir.
- No host GPU dependency; no model-weight symlinks.
