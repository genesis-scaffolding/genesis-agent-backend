# Plan (round 3): Radiance as a llama-swap engine — task `t-drv03p`

Worktree: `/home/gentran/Documents/worktrees/genesis-agent-backend/t-drv03p-radiance-27b`
Branch: `se/t-drv03p-radiance-27b` (uncommitted, as the stage requires)
Stage: plan-gate revision. No implementation.

---

## 0. Change log from round 2

Round 2 (`/tmp/se-tdrv03p-plan-r2.md`, journal `j-coe0k6`) was approved in shape
by Gen on 2026-11-05, with three required changes. This document is the
complete revised plan; the changes are:

1. **The kv-cache claim was wrong and is corrected.** Round 2 listed
   `kv_cache` among the llama-server fields with "no radiance equivalent".
   Radiance has `--kv-cache-dtype` (`bf16`, `fp8`, and on Qwen3.8-27B also
   `fp8_v4` / `k6_v4`). It is now a first-class tunable in the radiance
   layer: it flows through the same `override > recipe > option` cascade, is
   offered in the radiance override form where the llama-server kv-cache knob
   lives, and is rendered into the command. The two vocabularies stay
   separate (llama.cpp's `q4_0`/`q8_0` vs radiance's dtypes); the
   llama-server-only paragraph no longer swallows it.
2. **`--tp` is a full tunable**, not just a service option. It is in the
   override > recipe > option cascade, offered in the radiance override form,
   and rendered as `--tp N`.
3. **The recipe entry for the Qwen3.8 27B MXFP4 radiance model is required**,
   not optional. `services/llama_swap/data/recipes.yaml` is in the
   files-touched list as a required change, with the defaults the plan
   renders for that model.

Two further changes follow from the state of the tree, not from the feedback:

4. **`t-9xp8ik` has landed** (`79d9933`, merged into `main` and into this
   branch). The install path is no longer an assumption: the `docker_image`
   record kind, the seed file and the factory are in the tree. The plan now
   names the real files and the real service API, and the fallback paragraph
   is dropped.
5. **New gate baselines** on the moved base: `pytest -q` → 1320 passed, 37
   deselected; `ruff check` green; `ruff format --check` **green** (213 files
   — the three pre-existing failures were fixed by the merges); `pyright` 0
   errors.

Everything else from round 2 stands: the shape (radiance is an engine kind
inside the llama-swap plugin), the llama-swap command semantics, the
corrections to the reference config, the knob layering, the generated entry,
the display, the phases and the risk table.

---

## 1. What changed since round 1, and where I agree or differ

Round 1 was rejected on its shape, not its details: I recommended a standalone
declarative `radiance` service, Gen chose "extend llama-swap". Rounds 2 and 3
are built on that choice.

| Round-1 element | Round 2 and 3 |
|---|---|
| Standalone declarative service (`services/_declarative/radiance.yaml`) | Radiance is an **engine kind** inside the llama-swap plugin. The llama-swap config generator emits the entry; llama-swap supervises the container. |
| New declarative container fields (`devices`, `group_add`, `ulimits`, `init`, `stop_grace_period_s`, `runtime_endpoint_path`) in `ContainerSpec` | **Not needed.** The docker run command is a string the generator produces; the declarative service spec is untouched. |
| New `resolve_vault_model` pre-start hook | **Not needed.** The model path comes from the catalog (the piece path is already the resolved blob) at generation time, and is mounted as one file. |
| Cross-repo Bifrost change (`genesis-infrastructure-toolkit` filters `svc.name != "llama_swap"`) | **Not needed.** A radiance model is served *through* llama-swap's endpoint, so the gateway sees it as a llama-swap model. |
| Model selected by an option string with a hand-pasted path | The model is a catalog entry; the generator picks it up like any other model. |
| Radiance as its own service with its own install/start/stop UI | Radiance's image is an installable on the **existing** Binaries page; the model is configured in the **existing** Config editor. |

**Where I disagree with the rejection feedback, and say so.** Gen's brief
describes the radiance cmd as a docker run with `RENDER_GID=$(getent group
render | cut -d: -f3)`, `exec`, `--group-add ${RENDER_GID}`, `ttl: 10m`,
`healthCheckTimeout: 120s` inside the model, `--gpu-memory-utilization 0.92`,
`--ipc host --shm-size 16g`, and `-e HIP_VISIBLE_DEVICES=0`. I have read the
llama-swap source and the radiance docs, and **that command cannot work as
written**: llama-swap runs the `cmd` through `shlex.Posix` and `exec` with no
shell, refuses any `${...}` macro it does not define (`unknown macro
'${RENDER_GID}' found in models.<id>.cmd` fails the whole config load), decodes
`ttl` as an integer number of seconds, overwrites any per-model
`healthCheckTimeout` with the global one, and has no `--gpu-memory-utilization`
flag to pass through. `HIP_VISIBLE_DEVICES=0` also picks the APU iGPU on the
hybrid host rather than the R9700.

The plan therefore keeps Gen's shape and fixes the command. The corrections are
tabulated in section 3.2; each one is a load-time failure if left alone, so
they are not stylistic.

**Where I agree with the rejection.** The reasoning in round 1 ("shape 3 does
not fit") was wrong in its conclusion and right in its premises: llama-swap
does supervise containers, and its `cmd`/`cmdStop`/`checkEndpoint` schema is
exactly the right mechanism. The round-1 plan looked for a separate service
when the model belongs in the config llama-swap already owns.

---

## 2. Goal

A `.rad` model in the vault becomes an entry in the generated
`llama_swap/config.yaml` whose `cmd` starts the radiance container with the
model mounted and the AMD GPU passed through, whose `cmdStop` stops the
container, and whose `checkEndpoint` is `/v1/models`. llama-swap then serves
it through its own proxy, on demand, exactly as it serves a GGUF model.
Radiance's container image is installed through the worker, on the Binaries
page, like every other llama-swap artefact.

Scope: the install path and the config generation + display. No model
downloads, no on-box benchmark; Gen tests that by hand.

---

## 3. The findings that constrain the design

### 3.1 How llama-swap runs a command

From `mostlygeek/llama-swap` @ `main`:

- `internal/config/commands.go:SanitizeCommand` — strips `#` comment lines,
  turns a trailing `\` into a space, then splits with `shlex.Posix.Split`.
- `internal/process/process_command.go:533` — `exec.CommandContext(cmdCtx,
  args[0], args[1:]...)`. **No shell.** `$(...)`, `|`, `;`, `&&`, `exec` and
  shell variable assignment are not interpreted; they become literal argv.
- `internal/config/macros.go:validateMacroUses` — for a model's `cmd`,
  `cmdStop`, `proxy`, `checkEndpoint`, `name` and `description`, any
  `${NAME}` that is not a defined macro is a **config load error**:
  `unknown macro '${RENDER_GID}' found in models.<id>.cmd`. `${PORT}` and
  `${MODEL_ID}` are substituted before the check; `${env.VAR}` requires the
  variable to exist.
- `internal/config/load.go:143` — `modelConfig.HealthCheckTimeout =
  config.HealthCheckTimeout`: the per-model field is overwritten by the
  global, so the health-check budget is global-only.
- `config-schema.json` — `ttl` is `{"type":"integer","minimum":-1}`;
  `unloadTimeout` is a per-model integer; `checkEndpoint` matches
  `^/.*$|^none$`; `env` is an array of `NAME=value` strings; `proxy` defaults
  to `http://localhost:${PORT}`.
- `internal/config/load.go` decodes with `node.Decode(&config)` and the
  loader tolerates unknown fields (the generator already writes
  `generated_at`, `root`, `resolved_from`).

The upstream docs show the intended container pattern under "Docker and Podman
support using `cmd` and `cmdStop` together":

```yaml
"docker-llama":
  proxy: "http://127.0.0.1:${PORT}"
  cmd: |
    docker run --name ${MODEL_ID}
    --init --rm -p ${PORT}:8080 -v /mnt/nvme/models:/models
    ghcr.io/ggml-org/llama.cpp:server
    --model '/models/Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf'
  cmdStop: docker stop ${MODEL_ID}
```

### 3.2 Corrections to the reference config

| Reference | Why it fails | Correction |
|---|---|---|
| `RENDER_GID=$(getent group render \| cut -d: -f3)` / `exec docker run …` | no shell in llama-swap | plain argv list; the generator resolves the GIDs on the host and writes the numbers |
| `--group-add ${RENDER_GID}` | undefined macro → the whole config fails to load | `--group-add 992` (resolved number) |
| `ttl: 10m` | `ttl` is an integer number of seconds | `ttl: 600` (or `0`, the default) |
| `healthCheckTimeout: 120s` inside the model | overwritten by the global; `120s` does not decode into an `int` | raise the **global** `healthCheckTimeout` (it must cover a multi-minute load) |
| `--gpu-memory-utilization 0.92` | not a radiance flag; radiance's VRAM knob is `--gpu-headroom-mib` (default 96) | `--gpu-headroom-mib 96` |
| `--ipc host --shm-size 16g` | docker ignores `--shm-size` when `--ipc=host` | drop both (neither is needed) |
| `-e HIP_VISIBLE_DEVICES=0` | device 0 is the APU iGPU on the hybrid host; and `HIP_VISIBLE_DEVICES` indexes the post-`ROCR_VISIBLE_DEVICES` list, not the physical agents | `-e ROCR_VISIBLE_DEVICES=<physical index>`, no `HIP_VISIBLE_DEVICES` |
| `--cap-add=SYS_PTRACE` | not needed by radiance | drop |
| `--model /models/Qwen3.8-27B-Quark-AWQ-MXFP4-native.rad` | the .rad is nested in the vault's HF cache layout, not at the mount root | mount the resolved blob file at `/models/<filename>` |

### 3.3 Radiance facts that shape the command

Verified against the radiance repository's `docs/GUIDE.md` (flag table) and
`README.md` (serve-flag rows), fetched 2026-10-30:

| flag | default | guide row |
|---|---|---|
| `--model PATH` | required | the `.rad` container, a checkpoint, or a HF repo id |
| `--tp N` | 1 | tensor-parallel ranks |
| `--max-num-seqs N` | 256 | largest sequence count one step may carry |
| `--max-model-len N` | 0 | context bound; 0 takes the model's training context |
| `--kv-cache-dtype T` | bf16 | `bf16`, `fp8`, or on Qwen3.8-27B and Qwen3.8-Flash-Next `fp8_v4` or `k6_v4` |
| `--gpu-headroom-mib N` | 96 | VRAM left unclaimed on every card |
| `--host ADDR` | 0.0.0.0 | listen address |
| `--port N` | 8000 | listen port |

The guide's own serve row for the model this plan targets:
`Qwen3.8-27B FP8 and MXFP4 → --tp 2 --max-num-seqs 32 --max-model-len 200000
--kv-cache-dtype fp8` (two cards). This plan renders the one-card variant:
`--tp 1 --max-num-seqs 8 --max-model-len 32768 --kv-cache-dtype fp8
--gpu-headroom-mib 96`.

Other facts:

- Image: `stilldeadcode/radiance:latest` on Docker Hub, ~175 MB, kernels for
  `gfx1201`. `FROM scratch`: no shell, no `/etc/group` → docker cannot resolve
  group **names**, only numbers.
- Needs: `--device /dev/kfd --device /dev/dri`, the `render`/`video` groups,
  `--security-opt seccomp=unconfined` (Docker's default seccomp refuses the
  io_uring and NUMA-binding calls the ROCm runtime makes), `--init` (so
  SIGTERM reaches the engine and the cards are released), `--ulimit
  memlock=-1`, a 60 s stop grace.
- One model per process; OpenAI-compatible API (`/v1/chat/completions`,
  `/v1/completions`, `/v1/models`, plus Anthropic Messages and llama-server
  native). `/health`, `/v1/health`, `/ping` are liveness and are exempt from
  `--api-key`.
- Startup order reads *load, then serve*; a 19 GiB container takes minutes.
- The published `.rad` files are what radiance serves → no conversion.

### 3.4 The GPU index is a box fact

The R9700 is a discrete card at `0000:07:00.0`; radiance's own startup report
shows it as `rank 1 (device 1)` with the APU iGPU at device 0. The worker's
hardware snapshot enumerates AMD devices from `/sys/class/drm/card*/device/vendor`
in card order (`utils/collectors/hardware.py`), the same PCI order the ROCm
agent list uses — but same order is not same index once one device is hidden.
So the index is an explicit option, verified on the box with `rocminfo` inside
the container, and **not** taken from the ADR-039 device cascade (whose
`GGML_VK_VISIBLE_DEVICES` semantics do not apply to a containerised ROCr
engine).

---

## 4. The design

```
vault: models--StillDeadcode--qwen3.8-27b-mxfp4/blobs/<sha>   (the .rad)
   │  catalog (piece role "main", path already resolved)
   ▼
generate_config: engine_for_file(piece) → Engine.RADIANCE
   ├─ knobs:  override["radiance"] > recipe.radiance > LlamaSwapOptions.radiance
   ├─ image:  service.radiance_image_ref()
   ├─ gids:   getent group render / video, resolved on the host
   ▼
config.yaml model entry:  cmd / cmdStop / checkEndpoint / proxy / ttl / unloadTimeout
   ▼
llama-swap: starts `docker run`, proxies 127.0.0.1:${PORT} → container :8000
```

### 4.1 New module: `services/llama_swap/engines.py`

```python
class Engine(StrEnum):
    LLAMA_SERVER = "llama_server"
    RADIANCE = "radiance"


def engine_for_file(path: Path | None) -> Engine:
    """A ``.rad`` file is a radiance weight container; anything else is a llama-server GGUF."""
    if path is not None and path.suffix.lower() == ".rad":
        return Engine.RADIANCE
    return Engine.LLAMA_SERVER
```

`DetectedFileSet` (in `generate_config.py`) gains `engine: Engine =
Engine.LLAMA_SERVER`, so every existing construction site and every existing
test keeps its meaning. The engine is derived from the file, never configured.

### 4.2 New module: `services/llama_swap/radiance.py`

Holds everything radiance-specific, so `generate_config.py` does not grow a
second engine's vocabulary:

- `RadianceSettings` — the resolved knobs (frozen dataclass).
- `resolve_group_gids(names) -> tuple[tuple[str, str], ...]` — `getent group
  <name>` → `(name, gid)`; a group the host does not have is omitted.
- `render_radiance_cmd(settings, model_host_path, model_container_path) -> str`
  — the docker run command, in the repo's existing style (one flag group per
  line, trailing `\`).

The command, in full:

```
docker run --init --rm \
  --name ${MODEL_ID} \
  -p ${PORT}:8000 \
  --device /dev/kfd \
  --device /dev/dri \
  --group-add 992 \
  --group-add 44 \
  --security-opt seccomp=unconfined \
  --ulimit memlock=-1:-1 \
  -e ROCR_VISIBLE_DEVICES=1 \
  -v /home/…/blobs/<sha>:/models/qwen3.8-27b-mxfp4.rad:ro \
  stilldeadcode/radiance:latest \
  --model /models/qwen3.8-27b-mxfp4.rad \
  --host 0.0.0.0 \
  --port 8000 \
  --tp 1 \
  --max-num-seqs 8 \
  --max-model-len 32768 \
  --kv-cache-dtype fp8 \
  --gpu-headroom-mib 96
```

Notes on individual choices:

- **One file mounted, not the vault.** The catalog's piece path for a `.rad`
  is already the resolved blob (`sources/huggingface/source.py` resolves the
  snapshot symlink), so the mount source is a real file and the container sees
  a regular file at `/models/<original filename>`. Mounting the snapshot
  directory would break the HF cache's *relative* symlink inside the
  container; mounting the vault root would put the snapshot sha in the
  command.
- **The container port is fixed** (8000 inside, `-p ${PORT}:8000` outside).
  llama-swap assigns the host port from `startPort` (default 5800), so there
  is no port collision with the worker or with other services.
- **`--host 0.0.0.0`** inside the container, because llama-swap publishes the
  port with `-p` rather than running the container on the host network.
- **No `env:` array.** llama-swap's `env` is injected into the `docker`
  *client*'s environment, not into the container, so GPU visibility has to be
  a `-e` flag inside the command.

### 4.3 The knob layers

| layer | type | contents |
|---|---|---|
| service options | `LlamaSwapOptions.radiance` (`RadianceOptions`) | the deployment: container port, GPU index, group names, `tp`, `max_model_len`, `max_num_seqs`, `kv_cache_dtype`, `gpu_headroom_mib`, `ttl_s`, `unload_timeout_s`, `stop_grace_s`, `check_endpoint`, `extra_docker_args`, `extra_flags`. The **image is not an option**: it comes from the installed `docker_image` record (§4.6). |
| recipe | `Recipe.radiance` (`RadianceRecipe`) | `tp`, `max_model_len`, `max_num_seqs`, `kv_cache_dtype`, `gpu_headroom_mib`, `extra_flags` — the model family's profile |
| per-model override | `overrides.yaml` → `<entry_id>.radiance` | the same fields, per model |

Precedence `override > recipe > service option`, matching the existing fields.
`Recipe` gains an optional `radiance` block; the llama-server renderer never
reads it, and the radiance renderer never reads the llama-server fields, so the
two vocabularies cannot leak into one command.

**Each engine has its own vocabulary, and each knob is tunable on both
sides.** The radiance layer is the radiance counterpart of the llama-server
fields, not a copy of them:

| concept | llama-server field → flag | radiance field → flag |
|---|---|---|
| context bound | `ctx_size` → `-c N` | `radiance.max_model_len` → `--max-model-len N` |
| concurrent sequences | `parallel` → `--parallel N` | `radiance.max_num_seqs` → `--max-num-seqs N` |
| KV cache dtype | `kv_cache` (`q4_0` / `q8_0`) → `-ctk/-ctv` | `radiance.kv_cache_dtype` (`bf16` / `fp8` / `fp8_v4` / `k6_v4`) → `--kv-cache-dtype T` |
| tensor parallelism | (none) | `radiance.tp` → `--tp N` |
| VRAM headroom | (none) | `radiance.gpu_headroom_mib` → `--gpu-headroom-mib N` |
| escape hatch | `extra_flags` | `radiance.extra_flags` |

The kv cache is a real counterpart, not an absence: llama.cpp quantises the
KV cache with `-ctk q8_0 -ctv q8_0`; radiance quantises it with
`--kv-cache-dtype fp8`, and the guide notes it "halves what a token of context
costs". The two dtype vocabularies stay separate — a radiance model is never
offered `q4_0`, and a llama-server model is never offered `fp8`.

**The context bound is the one value the rest of the framework reads.** The
radiance renderer writes the resolved `max_model_len` into
`EvaluatedConfig.ctx_size`, so the pi export's `contextWindow` and the config
editor's context row are correct for a radiance model with no change to
`export_pi_config.py`. The llama-server `ctx_size` recipe/override field is
*not* consumed by the radiance renderer (it is llama.cpp's `-c`), and
`radiance.max_model_len` is not consumed by the llama-server renderer.

**The llama-server-only fields** (no counterpart in radiance, not rendered for
a radiance model, not offered in its override form) are: `kv_cache` in
llama.cpp's `q4_0`/`q8_0` vocabulary (its counterpart is
`radiance.kv_cache_dtype`, a separate field with a separate vocabulary),
`ctx_min` (llama.cpp's `--fit-ctx` floor), `ctx_size` (llama.cpp's `-c` cap),
`parallel` (llama.cpp's slot count), `mmproj_offload`, `spec`, `chat_template_file`,
`chat_template_kwargs`, `sampling`, `reasoning_budget`, `reasoning_budget_message`.
A `.rad` container carries its own tokeniser, chat template, drafter and
quantisation, so the template / sampling / MTP knobs have nothing to act on.

### 4.4 The generated entry

`build_entry` emits, for a radiance model:

```yaml
qwen3-8-27b-mxfp4:
  name: qwen3.8-27b-mxfp4
  cmd: |
    docker run --init --rm \
      …
  cmdStop: docker stop --time 60 ${MODEL_ID}
  checkEndpoint: /v1/models
  proxy: http://127.0.0.1:${PORT}
  ttl: 0
  unloadTimeout: 90
  resolved_from: radiance-qwen3-8-27b-mxfp4
```

- `cmdStop` is required: without it llama-swap's default SIGTERM kills the
  `docker run` client and leaves the container running. `--time 60` matches
  radiance's stop grace (it finishes the step in flight).
- `unloadTimeout: 90` > the `docker stop --time 60`, so llama-swap does not
  force-kill the docker client mid-shutdown.
- `checkEndpoint: /v1/models` — radiance's OpenAI-compatible model list, no
  API key needed.
- `ttl: 0` (never unload) matches what the generator already emits;
  `ttl: 600` is available per model when the GPU should be freed when idle.

### 4.5 The global health-check budget

Radiance's HTTP server starts after the model is loaded, and a 19 GiB load
takes minutes. llama-swap's health check is bounded by the **global**
`healthCheckTimeout` (the generator writes 60 today). We make it an option
(`LlamaSwapOptions.health_check_timeout_s`, default 600) and raise the
generated value.

The cost is stated plainly: a llama-server model that fails to come up is now
reported after up to ten minutes instead of one. If the box needs both, the
llama-server entries can set `checkEndpoint: none`.

### 4.6 The install path (t-9xp8ik, landed)

`t-9xp8ik` landed as `79d9933` and is in this branch. The machinery is in the
tree:

- `genesis_worker/utils/installables/records.py` — `DockerImageRecord`
  (`kind: "docker_image"`, `image`, `tag_pattern`, `default_tag`, plus the
  base fields `name`, `label`, `enabled`, `primary`, `variant`, `backend`).
- `genesis_worker/utils/installables/factory.py:build_installable` — builds a
  `DockerImageInstall` for a `docker_image` record, with
  `state_dir = <state_dir>/<record.name>`.
- `genesis_worker/utils/installables/docker_image.py:DockerImageInstall` —
  tag discovery via `list_image_tags`, install via `DockerPullAcquireSession`,
  the selected tag recorded in `<state_dir>/current`, `installed_version()`
  reading it back.
- `genesis_worker/services/llama_swap/data/installables.yaml` — the seed file
  (four `github_release` records today).
- `LlamaSwapService.installable_records()` — the merged records (seeds
  overridden by the user's);
  `LlamaSwapService.installs()` — the installables of the enabled records.

The radiance image is a seed record in that file:

```yaml
  - kind: docker_image
    name: radiance
    label: Radiance engine (container)
    image: stilldeadcode/radiance
    default_tag: latest
```

It appears on the Binaries page with the tag picker and pull progress like
any other installable, and the user can override the seed (different tag
pattern, or disable it) the same way they can override a llama-server seed.

**The image is not a service option.** It comes from the record, so the
installed tag always wins and there is one place that names the image. When
the radiance record is missing or disabled, `radiance_image_ref()` returns
`None`, `BuildOptions.radiance` is `None`, and the generator skips radiance
models entirely: turning the installable off turns radiance off. The config
editor says so instead of showing a model that cannot start.

The generated command runs the image that is installed. One method on the
service carries that:

```python
def radiance_image_ref(self) -> str | None:
    """The image ref the radiance command runs.

    The radiance ``docker_image`` record's repo, with the installed tag when
    one is recorded, else the record's default tag.
    """
    for record in self.installable_records():
        if record.kind != "docker_image" or record.name != RADIANCE_INSTALLABLE_NAME:
            continue
        if not record.enabled:
            return None
        installable = next(
            (i for i in self.installs() if i.name == record.name), None
        )
        tag = installable.installed_version() if installable is not None else None
        return f"{record.image}:{tag or record.default_tag}"
    return None
```

Note that `DockerImageInstall.image_ref` reports the *configured* tag, not the
installed one, so the method composes the ref itself from the record plus
`installed_version()`. `RADIANCE_INSTALLABLE_NAME = "radiance"` is a module
constant in `services/llama_swap/radiance.py`.

### 4.7 The service wiring

`LlamaSwapService`:

- `_build_options()` resolves the radiance settings: the image ref (from
  `radiance_image_ref()`), the group GIDs (from `getent`), and the service
  option defaults, and puts them on `BuildOptions`. When there is no enabled
  radiance record, `BuildOptions.radiance` is `None` and the generator skips
  radiance models.
- `BuildOptions` gains `radiance: RadianceSettings | None` and
  `health_check_timeout_s: int = 60`.
- `is_ready_to_serve()` is unchanged: it gates the "Regenerate config" button
  on a llama-server binary. The radiance image being absent does not block
  regeneration (the config is complete either way); the Binaries page shows
  the install state.
- `runtime_endpoint()` is unchanged: llama-swap's endpoint serves the
  radiance model too.

### 4.8 The display

`ui/config_editor.py`:

- The entry label shows the engine: `qwen3-8-27b-mxfp4 → radiance (container)
  (recipe: radiance-qwen3-8-27b-mxfp4)`.
- The "Files" block shows the host model path and the container path.
- The "Effective configuration" table renders the radiance fields (engine,
  image, container model path, GPU index, resolved group GIDs, `tp`,
  `max_model_len`, `max_num_seqs`, `kv_cache_dtype`, `gpu_headroom_mib`,
  `ttl`, `unloadTimeout`, `checkEndpoint`) instead of the llama-server ones.
  The llama-server rendering is untouched.
- The override form branches on the engine:
  - **llama-server branch** (unchanged): binary, device, KV cache
    (`q4_0`/`q8_0`), parallel, fit context, context size, reasoning, chat
    template, sampling, extra llama.cpp flags, spec.
  - **radiance branch** (new): max model len, max num seqs, **TP**, **KV
    cache dtype**, GPU headroom MiB, extra radiance flags — the last two
    positioned where the llama-server form has its kv-cache and extra-flags
    widgets, so the two engines' forms line up side by side.
- The "Raw cmd + env" block shows the docker run command.

The radiance override form's KV-cache widget is a selectbox over radiance's
dtype vocabulary (`bf16`, `fp8`, `fp8_v4`, `k6_v4`), not a free-text field:
the guide notes that `fp8_v4` and `k6_v4` are accepted only on Qwen3.8-27B
and Qwen3.8-Flash-Next, so the widget's help text says so and the value is
still overridable by hand through the recipes overlay.

`export_pi_config.py` needs no change: a radiance model's `contextWindow`
comes from `EvaluatedConfig.ctx_size`, which the radiance renderer populates
from `max_model_len`. **Known gap:** a radiance model with a vision tower is
exported as text-only, because the catalog carries no separate `mmproj` piece
for a `.rad` container (the tower is inside it).

---

## 5. Phases

Each phase is independently testable and ends at the four-command gate.

**Phase 1 — the engine concept and the radiance renderer.**
`services/llama_swap/engines.py` (Engine, `engine_for_file`),
`services/llama_swap/radiance.py` (`RadianceSettings`, `resolve_group_gids`,
`render_radiance_cmd`), `DetectedFileSet.engine`, the `_is_llm_candidate`
change, the `.rad` extension stripping in `walk_models` and
`make_display_name`, `EvaluatedConfig.engine`, the dispatch in
`evaluate_recipe`, the engine branch in `build_entry`.

**Phase 2 — the knob layers and the display.**
`LlamaSwapOptions.radiance` (+ `health_check_timeout_s`), `Recipe.radiance`
(the `tp` / `max_model_len` / `max_num_seqs` / `kv_cache_dtype` /
`gpu_headroom_mib` / `extra_flags` block), the override block, the service's
`radiance_image_ref()` and `_build_options()`, the config editor's per-engine
rendering and override form, `emit_payload`'s global `healthCheckTimeout`.

**Phase 3 — the seeds and the end-to-end tests.**
The radiance `docker_image` seed record in `data/installables.yaml`, the
required radiance recipe in `data/recipes.yaml`, the full test suite below,
and the docs (the README's llama-swap section).

---

## 6. Files touched

**New**

| File | Change |
|---|---|
| `genesis_worker/services/llama_swap/engines.py` | `Engine` enum, `engine_for_file()` |
| `genesis_worker/services/llama_swap/radiance.py` | `RadianceSettings`, `resolve_group_gids()`, `render_radiance_cmd()`, `RADIANCE_INSTALLABLE_NAME` |
| `genesis_worker/tests/test_radiance_engine.py` | the engine detection, the group resolution, the command rendering, the knob cascade |

**Modified**

| File | Change |
|---|---|
| `genesis_worker/services/llama_swap/generate_config.py` | `DetectedFileSet.engine`, `EvaluatedConfig.engine`, `_is_llm_candidate` accepts `.rad`, `.rad` extension stripping, the engine dispatch in `evaluate_recipe`, the engine branch in `build_entry`, `BuildOptions.radiance` + `health_check_timeout_s`, `emit_payload`'s global health-check timeout |
| `genesis_worker/services/llama_swap/radiance.py` (new) | see above |
| `genesis_worker/services/llama_swap/recipes.py` | `Recipe.radiance` (`RadianceRecipe`: `tp`, `max_model_len`, `max_num_seqs`, `kv_cache_dtype`, `gpu_headroom_mib`, `extra_flags`) |
| `genesis_worker/services/llama_swap/options.py` | `LlamaSwapOptions.radiance` (`RadianceOptions`), `health_check_timeout_s` |
| `genesis_worker/services/llama_swap/service.py` | `radiance_image_ref()`, the radiance settings in `_build_options()` |
| `genesis_worker/services/llama_swap/ui/config_editor.py` | the per-engine label, the effective table, the per-engine override form (including the radiance TP and KV-cache-dtype widgets) |
| **`genesis_worker/services/llama_swap/data/recipes.yaml`** | **required**: the radiance recipe for the Qwen3.8 27B MXFP4 model — see 6.1 |
| **`genesis_worker/services/llama_swap/data/installables.yaml`** | **required**: the radiance `docker_image` seed record — see 6.2 |
| `genesis_worker/tests/test_generate_config.py` | the radiance entry evaluation, the knob cascade |
| `genesis_worker/tests/test_config_emit.py` | the emitted YAML for a radiance entry |
| `genesis_worker/tests/test_service_llama_swap.py` | the service wiring, `radiance_image_ref()` |
| `genesis_worker/tests/test_llama_swap_installables.py` | the radiance seed record is present and builds a docker installable |
| `README.md` | a short note on radiance as a llama-swap engine |

**Not touched:** `contracts/` (no contract change), `settings.py`, `facade.py`,
`registries.py`, `api/`, `utils/services/` (the declarative service spec is
untouched, which is the point of the reversal), `utils/installables/` (the
landed machinery is used as it is), `utils/process/docker.py`.

### 6.1 The required radiance recipe

`genesis_worker/services/llama_swap/data/recipes.yaml`, added as a
matchable recipe:

```yaml
  # Qwen3.8 27B MXFP4 (AMD Quark AWQ) as a radiance container. The .rad
  # carries the weights, the tokeniser, the chat template and the DFlash2
  # drafter; these are the only knobs the engine still needs.
  # One R9700 (31.86 GiB VRAM) holds the 19 GiB container, so the context
  # bound and the sequence count are the one-card values, not upstream's
  # two-card row (--tp 2 --max-num-seqs 32 --max-model-len 200000).
  radiance-qwen3-8-27b-mxfp4:
    match: "qwen3.8-27b-mxfp4"
    radiance:
      tp: 1
      max_model_len: 32768
      max_num_seqs: 8
      kv_cache_dtype: fp8
      gpu_headroom_mib: 96
```

The match keyword is the container's own name, normalised by the resolver
(lowercase, `-`/`_`/`.` stripped): the catalog entry name is
`StillDeadcode/qwen3.8-27b-mxfp4` → `qwen3827bmxfp4`, and the keyword
`qwen3.8-27b-mxfp4` → `qwen3827bmxfp4`. It does not shadow the bundled
`qwen3.6*` keywords, and no bundled keyword matches it.

The values equal the service option defaults today, so the recipe is the
*editable family profile* rather than a behavioural change: when the box's
numbers differ (a second card, a different VRAM budget), the recipe is where
they change, and the override is where one model diverges.

### 6.2 The required radiance installable seed

`genesis_worker/services/llama_swap/data/installables.yaml`, appended to the
`installables:` list:

```yaml
  - kind: docker_image
    name: radiance
    label: Radiance engine (container)
    image: stilldeadcode/radiance
    default_tag: latest
```

The record has no `variant` and no `backend`: the variant keys are the
llama-server binary variants (`cuda`, `cpu`, `vulkan`), and the radiance
container is not one of them. `primary` stays false — the llama-swap proxy
remains the service's primary installable.

---

## 7. QA plan

The repo gate from `AGENTS.md`, all four must pass:

```
uv run ruff check --fix genesis_worker
uv run ruff format genesis_worker
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

Baseline on this branch (after `79d9933` and `e8a621a` landed): `pytest -q` →
**1320 passed, 37 deselected**; `ruff check` → all checks passed; `ruff format
--check` → **213 files already formatted (green)**; `pyright` → 0 errors, 0
warnings, 0 informations. The three pre-existing format failures round 2
reported are gone, fixed by the merges. If a future merge re-dirties the
format gate, run `ruff format` and say so in the PR; do not ship a red gate
silently.

New tests, by name:

`genesis_worker/tests/test_radiance_engine.py`

- `test_engine_for_file_detects_rad`
- `test_engine_for_file_defaults_to_llama_server`
- `test_engine_for_file_handles_none`
- `test_resolve_group_gids_returns_numbers_for_known_groups`
- `test_resolve_group_gids_omits_unknown_group`
- `test_resolve_group_gids_returns_empty_when_getent_missing`
- `test_render_radiance_cmd_contains_docker_run_and_model_mount`
- `test_render_radiance_cmd_uses_numeric_group_ids`
- `test_render_radiance_cmd_has_no_shell_metacharacters`
- `test_render_radiance_cmd_uses_rocr_visible_devices_only`
- `test_render_radiance_cmd_publishes_port_and_binds_container_port`
- `test_render_radiance_cmd_renders_tp_flag`
- `test_render_radiance_cmd_renders_kv_cache_dtype_flag`
- `test_render_radiance_cmd_renders_max_model_len_and_max_num_seqs`
- `test_render_radiance_cmd_renders_gpu_headroom_flag`

`genesis_worker/tests/test_generate_config.py`

- `test_rad_piece_is_an_llm_candidate`
- `test_rad_entry_id_strips_the_rad_extension`
- `test_rad_display_name_strips_the_rad_extension`
- `test_rad_entry_evaluates_to_the_radiance_engine`
- `test_rad_entry_knobs_prefer_override_over_recipe_over_option`
- `test_rad_entry_kv_cache_dtype_follows_the_cascade`
- `test_rad_entry_tp_follows_the_cascade`
- `test_rad_entry_max_model_len_lands_in_ctx_size`
- `test_rad_entry_ignores_llama_server_recipe_fields`
- `test_rad_entry_ignores_the_llama_server_kv_cache_vocabulary`
- `test_llama_server_entry_is_unchanged_by_the_engine_branch`

`genesis_worker/tests/test_config_emit.py`

- `test_rad_entry_emits_cmd_stop_and_check_endpoint`
- `test_rad_entry_emits_unload_timeout_longer_than_the_stop_grace`
- `test_rad_entry_emits_no_env_array`
- `test_emitted_global_health_check_timeout_follows_the_option`
- `test_llama_server_entry_payload_is_unchanged`

`genesis_worker/tests/test_service_llama_swap.py`

- `test_radiance_image_ref_uses_the_installed_tag`
- `test_radiance_image_ref_falls_back_to_the_default_tag`
- `test_radiance_image_ref_is_none_without_the_radiance_record`
- `test_radiance_image_ref_is_none_when_the_record_is_disabled`
- `test_build_options_carries_the_radiance_settings`
- `test_build_options_has_no_radiance_settings_without_the_record`

`genesis_worker/tests/test_llama_swap_installables.py`

- `test_seed_records_include_the_radiance_docker_image`
- `test_radiance_record_builds_a_docker_image_installable`

Use `service_ctx(tmp_path)` from `genesis_worker/tests/_factories.py` and the
existing catalog factories; no network, no docker.

Manual checks, after the gate is green (the DoD is a runtime behaviour, so it
needs a human — Gen is testing this by hand):

1. Binaries page → the `radiance` entry → Install → the pull progress renders
   → the version dropdown shows the tag; the image is present
   (`docker image inspect stilldeadcode/radiance:<tag>`).
2. Config editor → the radiance entry appears → the effective table shows the
   image, the GPU index, the group GIDs, `tp`, `max_model_len`,
   `max_num_seqs`, `kv_cache_dtype`, `gpu_headroom_mib` → the raw cmd shows
   the docker run with the resolved numbers.
3. The override form → change the KV cache dtype to `bf16` and the TP to 2,
   save, regenerate → the cmd carries `--kv-cache-dtype bf16` and `--tp 2`.
4. The generated `config.yaml` → the entry has `cmd`, `cmdStop`,
   `checkEndpoint`, `proxy`, `ttl`, `unloadTimeout`; the whole file loads in
   llama-swap (a config error shows up in llama-swap's log, not in the
   worker's).
5. Start llama-swap, request the model → the container starts → the radiance
   startup report shows the card line, the VRAM receipt and the FINDINGS
   warnings → `/v1/models` lists it → a chat completion comes back.
6. Unload the model → `cmdStop` runs → the container stops and is removed
   (`--rm`).
7. `GET /v1/services` on the worker → llama-swap's `runtime_endpoint` is
   unchanged → Bifrost needs no change.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| The generated command contains host facts (group GIDs, GPU index) | The config is generated per node by the worker; the values are re-resolved on every regeneration |
| The wrong GPU is selected on the hybrid host (device 0 is the APU iGPU) | `gpu_index` is an explicit option, verified with `rocminfo` inside the container; a wrong index fails loudly (radiance has no gfx1151 kernel) rather than silently using the iGPU |
| The global `healthCheckTimeout` has to grow, slowing failure reporting for llama-server models | It is an option; `checkEndpoint: none` is available per model as the escape hatch |
| Radiance's HTTP server may only start listening after the load, so the health check sees connection-refused for minutes | The global budget covers it; `/load` is the endpoint to look at if a progress-reporting readiness check turns out to be possible |
| `--group-add` with numbers vs names | Upstream's own compose resolves the numbers with `getent`; the radiance image is `FROM scratch` and has no `/etc/group`, so docker cannot resolve names |
| Binding a single file into a `FROM scratch` image: docker must create `/models/` and the target file | Verify with a throwaway container before the first real run; the alternative (mount the whole `models--<owner>--<repo>` dir) puts the snapshot sha in the command |
| The two dtype vocabularies get crossed (`q4_0` reaching radiance, `fp8` reaching llama-server) | Separate fields, separate renderers, separate override widgets; two tests pin each direction (`test_rad_entry_ignores_the_llama_server_kv_cache_vocabulary` and its llama-server mirror) |
| `fp8_v4` / `k6_v4` are refused by an architecture that does not accept them | The guide restricts them to Qwen3.8-27B and Qwen3.8-Flash-Next; the override widget's help text says so, and the value stays hand-editable through the recipes overlay |
| The radiance recipe's keyword could shadow a bundled recipe, or be shadowed | The keyword is longer than any bundled `qwen3.8*` keyword and no bundled keyword matches it; the resolver's shadowing rule keeps the longest match |
| The pi export marks a radiance model as text-only even when it has vision | Documented as a known gap in this round; the catalog carries no `mmproj` piece for a `.rad` |

---

## 9. What I did not finish

- **At plan time, no implementation, no commit.** The only file written
  then was the revised ADR, now
  `docs/arch/adr-044-radiance-as-llama-swap-engine.md`; it was renumbered
  after t-sgqw36 landed as 043.
- **At plan time, there was no plan file in `docs/arch/plans/`.** This approved
  plan text is now recorded in the repository at
  `docs/arch/plans/plan-044-radiance-as-llama-swap-engine.md`.
- **The ADR is Proposed.** It carries a recommended default for each open
  point; the approval gate picks.
- **Nothing was run on the box.** No image pulled, no model downloaded, no
  container started. The GPU index, the ROCm/driver compatibility, the VRAM
  budget and the load time are unmeasured. Gen tests those by hand.
- **The radiance flag names are verified, not assumed.** `--kv-cache-dtype`
  (`bf16`, `fp8`, `fp8_v4`, `k6_v4`), `--tp`, `--max-num-seqs`,
  `--max-model-len` and `--gpu-headroom-mib` come from the radiance
  repository's `docs/GUIDE.md` flag table and the README's serve-flag rows,
  fetched 2026-10-30. Nothing in this revision rests on a flag Gen supplied.
- **The ADR's "no radiance equivalent" sentence is corrected** in this
  revision: the kv cache has a counterpart (`radiance.kv_cache_dtype` →
  `--kv-cache-dtype`), and the llama-server-only list no longer contains it.
