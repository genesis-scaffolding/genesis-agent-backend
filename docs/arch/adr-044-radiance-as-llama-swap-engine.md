# ADR-044: Radiance as a llama-swap engine — a container-backed model kind in the llama-swap config generator

## Title

Radiance as a llama-swap engine: the llama-swap config generator learns a second
engine kind whose `cmd` is a `docker run` of the radiance container, supervised
by llama-swap through `cmdStop` and `checkEndpoint`.

## Status

Proposed. The shape was decided by Gen on 2026-11-01 (rework round 1): extend
llama-swap rather than add a standalone service. The points marked "open" below
carry a recommended default; approving this ADR confirms them.

This ADR supersedes the withdrawn round-1 radiance ADR
(`adr-043-radiance-service-shape.md`, a standalone declarative service).

Revision note (plan round 3, 2026-11-05): the kv-cache claim in §3 is
corrected — radiance has `--kv-cache-dtype`, so the kv cache is a tunable on
both engines with separate vocabularies — and the t-9xp8ik dependency in §"The
dependency on t-9xp8ik" and §7 is restated against landed code.

## Context

### The reversal

The round-1 plan proposed radiance as a standalone declarative service: a
`radiance.yaml` in `services/_declarative/`, a new hook kind to resolve the
`.rad` file out of the vault, and six new fields on the declarative container
spec (`devices`, `group_add`, `ulimits`, `init`, `stop_grace_period_s`,
`runtime_endpoint_path`).

Gen rejected that shape and chose the third of the three options the task
offered: **extend llama-swap so radiance becomes one of the things llama-swap
can deploy, alongside the llama-server binaries it already manages.** Radiance
is one kind of model inference engine that llama-swap starts, exactly as
llama.cpp is.

The consequence that decides the rest of the design: llama-swap already
supervises processes, proxies them, health-checks them, swaps them on demand,
and publishes them to pi-agent and to Bifrost. A radiance model served through
llama-swap inherits all of it. The round-1 plan's cross-repo dependency (the
Bifrost provider filter in `genesis-infrastructure-toolkit`, which only routes
`llama_swap` services) **disappears**: radiance models are served *through*
llama-swap's own endpoint, so the gateway story does not change.

### What radiance is, from its own docs

- A container image exists: `stilldeadcode/radiance` on Docker Hub, `latest`,
  about 175 MB, kernels built for `gfx1201` (the R9700). The image is
  `FROM scratch` plus the ROCm runtime, a static busybox, curl and jq. There
  is no shell and no `/etc/group`.
- The entrypoint is `radiance`. It serves **one model per process**:
  `radiance --model /path/to/model.rad --port 8000 --host 0.0.0.0 --tp 1 ...`.
  `--model` takes a `.rad` container path, a checkpoint directory, or a
  HuggingFace repo id it fetches itself. There is no env var and no config
  file for the model.
- The published `.rad` files on HuggingFace are exactly what radiance serves,
  so the `.rad` files the worker already acquires need **no conversion**.
- The HTTP API is OpenAI-compatible: `/v1/chat/completions`,
  `/v1/completions`, `/v1/models`, `/v1/images/*`, `/v1/audio/*`,
  `/v1/videos`, plus Anthropic's Messages API and llama-server's native one.
  Liveness is `GET /health` (also `/v1/health`, `/ping`), exempt from
  `--api-key`.
- GPU visibility is `ROCR_VISIBLE_DEVICES` (the engine's own compose files use
  it to pin one card). `--tp N` splits the model across N cards.
- The tuning flags the engine exposes, from the repository's `docs/GUIDE.md`
  flag table: `--tp N` (tensor-parallel ranks, default 1), `--max-num-seqs N`
  (default 256), `--max-model-len N` (context bound, 0 = the model's training
  context), `--kv-cache-dtype T` (`bf16`, `fp8`, and on Qwen3.8-27B and
  Qwen3.8-Flash-Next also `fp8_v4` and `k6_v4`; the guide notes the fp8 cache
  "halves what a token of context costs"), `--gpu-headroom-mib N` (VRAM left
  unclaimed per card, default 96), `--host ADDR`, `--port N`. The guide's own
  serve row for the two-card Qwen3.8-27B case is `--tp 2 --max-num-seqs 32
  --max-model-len 200000 --kv-cache-dtype fp8`.
- The container needs, per its own `docs/DOCKER.md` and
  `deploy/compose/common.yaml`: `--device /dev/kfd --device /dev/dri`, the
  `render` and `video` groups, `--security-opt seccomp=unconfined` (Docker's
  default seccomp profile refuses the io_uring and NUMA-binding calls the
  ROCm runtime makes), `--init`, `--ulimit memlock=-1`, and a 60 s stop grace
  (the engine finishes the step in flight before releasing the cards).

### What llama-swap actually does with a `cmd`

Read from the upstream source (`mostlygeek/llama-swap`, `main`), because these
four facts decide the shape of the generated entry and four of them contradict
the reference config Gen pasted:

1. **There is no shell.** `internal/config/commands.go:SanitizeCommand` splits
   the `cmd` with `shlex.Posix.Split` (comments stripped, trailing `\` turned
   into a space) and `internal/process/process_command.go` runs
   `exec.CommandContext(ctx, args[0], args[1:]...)`. `$(getent group render)`,
   `|`, `;`, `&&`, `exec` and variable assignment do not work. The `cmd` is a
   plain argv list.
2. **Undefined macros are a hard error.**
   `internal/config/macros.go:validateMacroUses` rejects any `${NAME}` in a
   model's `cmd`, `cmdStop`, `proxy` or `checkEndpoint` that is not a defined
   macro: `unknown macro '${RENDER_GID}' found in models.<id>.cmd`. Only
   `${PORT}` and `${MODEL_ID}` are substituted (plus user macros and
   `${env.VAR}`, which must exist in the environment). So the render and video
   GIDs must be **baked into the generated command as literal numbers**.
3. **`ttl` is an integer number of seconds** (`config-schema.json`:
   `{"type": "integer", "minimum": -1}`). `ttl: 10m` does not decode.
4. **`healthCheckTimeout` is global.** The field exists on the Go model struct,
   but `internal/config/load.go` overwrites every model's copy with the global
   value. A per-model `healthCheckTimeout: 120s` also fails to decode (the
   field is an `int`, the value is a string). The per-model knob that does
   exist is `unloadTimeout` (graceful unload timeout in seconds), which is
   exactly what a slow `docker stop` needs.

The upstream schema also documents the pattern this design uses:
`cmd: docker run --name ${MODEL_ID} ...` with `cmdStop: docker stop
${MODEL_ID}`, under the heading "Docker and Podman support using `cmd` and
`cmdStop` together".

### Corrections to the reference config

Gen's reference config (pasted from the internet) is not loadable by
llama-swap and is not valid for radiance. The corrections:

| Reference | Problem | Correction |
|---|---|---|
| `RENDER_GID=$(getent group render \| cut -d: -f3)` … `exec docker run …` | no shell; `$(...)`, `\|` and `exec` are not executed | plain argv list; the GIDs are resolved on the host by the generator and emitted as numbers (the radiance image is `FROM scratch`, so docker cannot resolve group names either) |
| `--group-add ${RENDER_GID}` | undefined macro: llama-swap refuses to load the config | `--group-add 992` (the resolved number) |
| `ttl: 10m` | `ttl` is an integer number of seconds | `ttl: 600` |
| `healthCheckTimeout: 120s` inside the model | per-model value is overwritten by the global; the string does not decode into an `int` | the **global** `healthCheckTimeout` is raised (it must cover a multi-minute radiance load) |
| `--gpu-memory-utilization 0.92` | not a radiance flag; radiance's VRAM knob is `--gpu-headroom-mib` (default 96) | `--gpu-headroom-mib 96` |
| `--ipc host --shm-size 16g` | docker ignores `--shm-size` when `--ipc=host` is set | one or the other; neither is needed for radiance |
| `-e HIP_VISIBLE_DEVICES=0` | on the hybrid host device 0 is the APU iGPU, not the R9700; and `HIP_VISIBLE_DEVICES` indexes the post-`ROCR_VISIBLE_DEVICES` list, not the physical agents | `-e ROCR_VISIBLE_DEVICES=<physical index>` and no `HIP_VISIBLE_DEVICES` |
| `--cap-add=SYS_PTRACE` | not needed by radiance | dropped |

The GPU index is the one number that must be read off the box rather than
guessed: radiance's own startup report shows the R9700 at `0000:07:00.0` as
`rank 1 (device 1)`, with the APU iGPU at device 0.

### The dependency on t-9xp8ik — landed

`t-9xp8ik` landed as `79d9933` and is in this branch. It turned llama-swap's
installables into declarative records with a `kind` discriminator
(`github_release` | `docker_image`), the four built-ins as seed records, and
the docker flavour built on the existing `DockerImageInstall` /
`DockerPullAcquireSession` machinery:

- `utils/installables/records.py` — `DockerImageRecord` (`kind`, `image`,
  `tag_pattern`, `default_tag`).
- `utils/installables/factory.py:build_installable` — builds a
  `DockerImageInstall` for a `docker_image` record, with its state dir under
  the record's name.
- `utils/installables/docker_image.py:DockerImageInstall` — tag discovery,
  pull progress, the selected tag recorded in `<state_dir>/current`,
  `installed_version()` reading it back.
- `services/llama_swap/data/installables.yaml` — the seed file.
- `LlamaSwapService.installable_records()` / `.installs()` — the merged
  records and their installables.

This design uses that shape: radiance's container image is an installable of
kind `docker_image`, installed from the Binaries page like any other, and the
generated command runs the image that is installed.

## Decision

We will add radiance as a second **engine kind** inside the llama-swap plugin.
The llama-swap config generator, the recipe cascade, the per-model overrides,
the config editor and the pi export all learn about engines; the framework
outside the plugin does not change.

### 1. The engine is a property of the model file

`genesis_worker/services/llama_swap/engines.py`:

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

`DetectedFileSet` gains `engine: Engine = Engine.LLAMA_SERVER`, so every
existing construction site keeps working and every existing test keeps its
meaning. The engine is derived, not configured: there is no way to point a
GGUF at radiance or a `.rad` at llama-server, and no need for one.

### 2. The catalog walk sees `.rad` entries

`generate_config._is_llm_candidate` currently requires a `.gguf` piece for
HuggingFace entries, so a `.rad`-only repository is invisible to the config
generator today. It accepts an entry that carries a `.gguf` **or** a `.rad`
piece. `walk_models` strips the `.rad` extension from the piece name before
building the entry id, exactly as it strips `.gguf`, so the model id is
`qwen3-8-27b-mxfp4` and not `qwen3-8-27b-mxfp4-rad`. `make_display_name`
strips it too.

The catalog's role vocabulary already carries this: `contracts/classify.py`
lists `.rad` among the weight extensions, so a `.rad` piece's role is `main`
and it is picked up as the entry's main piece.

### 3. The knobs are layered, and the layers are engine-specific

| layer | what it holds |
|---|---|
| service options (`LlamaSwapOptions.radiance`) | the deployment: container port, GPU index, group names, `tp`, `max_model_len`, `max_num_seqs`, `kv_cache_dtype`, `gpu_headroom_mib`, `ttl`, `unloadTimeout`, `checkEndpoint`, extra docker args and extra engine flags. The image is not an option: it comes from the installed `docker_image` record (§7). |
| recipe (`Recipe.radiance`) | the model family: the same tuning fields, for a family of models |
| per-model override (`overrides.yaml`, `radiance:` block) | the same tuning fields, for one model |

The cascade is `override > recipe > service option`, the same precedence the
existing fields use. `Recipe` gains an optional `radiance` block; the
llama-server renderer never reads it and the radiance renderer never reads the
llama-server fields, so the two vocabularies cannot leak into each other.

**Each engine has its own vocabulary, and each concept is tunable on both
sides.** The radiance layer is the counterpart of the llama-server fields, not
a copy of them:

| concept | llama-server field → flag | radiance field → flag |
|---|---|---|
| context bound | `ctx_size` → `-c N` | `radiance.max_model_len` → `--max-model-len N` |
| concurrent sequences | `parallel` → `--parallel N` | `radiance.max_num_seqs` → `--max-num-seqs N` |
| KV cache dtype | `kv_cache` (`q4_0` / `q8_0`) → `-ctk` / `-ctv` | `radiance.kv_cache_dtype` (`bf16` / `fp8` / `fp8_v4` / `k6_v4`) → `--kv-cache-dtype T` |
| tensor parallelism | (none) | `radiance.tp` → `--tp N` |
| VRAM headroom | (none) | `radiance.gpu_headroom_mib` → `--gpu-headroom-mib N` |
| escape hatch | `extra_flags` | `radiance.extra_flags` |

The kv cache is a real counterpart, not an absence: llama.cpp quantises the KV
cache with `-ctk q8_0 -ctv q8_0`, radiance with `--kv-cache-dtype fp8`, and
the radiance guide notes that the fp8 cache "halves what a token of context
costs". The two dtype vocabularies stay separate — a radiance model is never
offered `q4_0`, a llama-server model is never offered `fp8` — and the
`fp8_v4` / `k6_v4` dtypes the guide restricts to Qwen3.8-27B and
Qwen3.8-Flash-Next are called out in the override widget's help text.

The llama-server-only fields (no counterpart in radiance, not rendered for a
radiance model, not offered in its override form) are: `kv_cache` in
llama.cpp's `q4_0` / `q8_0` vocabulary (its counterpart is
`radiance.kv_cache_dtype`, a separate field with a separate vocabulary),
`ctx_min` (llama.cpp's `--fit-ctx` floor), `ctx_size` (llama.cpp's `-c` cap),
`parallel` (llama.cpp's slot count), `mmproj_offload`, `spec`,
`chat_template_file`, `chat_template_kwargs`, `sampling`, `reasoning_budget`,
`reasoning_budget_message`. A `.rad` container carries its own tokeniser,
chat template, drafter and quantisation, so the template / sampling / MTP
knobs have nothing to act on.

The context bound is the one value the rest of the framework reads: the
radiance renderer writes the resolved `max_model_len` into
`EvaluatedConfig.ctx_size`, so the pi export's `contextWindow` and the config
editor's context row are correct for a radiance model with no change to
`export_pi_config.py`. The llama-server `ctx_size` recipe and override fields
are not consumed by the radiance renderer, and `radiance.max_model_len` is not
consumed by the llama-server renderer.

### 4. The generated command

`genesis_worker/services/llama_swap/radiance.py` renders the radiance command.
The shape is llama-swap's own documented docker pattern, with the reference
config's corrections applied:

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
  -v /home/…/models--StillDeadcode--qwen3.8-27b-mxfp4/blobs/<sha>:/models/qwen3.8-27b-mxfp4.rad:ro \
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

Decisions inside that command:

- **The model is bind-mounted as one file.** The catalog's piece path for a
  `.rad` is already the resolved blob (`sources/huggingface/source.py`
  resolves the snapshot symlink), so the mount source is the real file and the
  container sees a regular file at `/models/<filename>`. Mounting the snapshot
  directory instead would break the HF cache's relative symlink inside the
  container.
- **The container's port is fixed** (`--port 8000` inside, `-p ${PORT}:8000`
  outside). llama-swap owns the host port and assigns it from `startPort`.
- **The group GIDs are resolved on the host** at generation time with
  `getent group <name>`, and emitted as numbers. A group that does not exist
  on the host is omitted rather than guessed.
- **`ROCR_VISIBLE_DEVICES` is the only GPU-visibility variable.** It takes the
  physical agent index. `HIP_VISIBLE_DEVICES` indexes the post-ROCR list and
  must not be set to the physical index.
- **`--host 0.0.0.0`** inside the container, because llama-swap publishes the
  port with `-p` rather than running the container on the host network.

### 5. The generated entry

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
  resolved_from: radiance-qwen3-8-27b
```

- `cmdStop` stops the container. Without it, llama-swap's default SIGTERM
  would kill the `docker run` client and leave the container running.
  `--time 60` matches radiance's stop grace.
- `unloadTimeout: 90` is longer than the `docker stop --time 60`, so
  llama-swap does not force-kill the docker client while the container is
  still shutting down.
- `checkEndpoint: /v1/models` is radiance's OpenAI-compatible model list and
  needs no API key.
- `ttl: 0` (never unload) is the default, matching what the generator already
  emits for llama-server models. A per-model `ttl` in seconds is available
  when the GPU should be freed when idle.
- The entry carries no `env` array: llama-swap's `env` is injected into the
  `docker` **client**'s environment, not into the container, so GPU
  visibility has to be a `-e` flag inside the command.

### 6. The health-check budget is global, so it is raised

Radiance's startup order reads *load, then serve* (its guide: "claims the
card, splits it between weights and KV cache, loads, and serves on
http://0.0.0.0:8000"), and a 19 GiB container takes minutes to load. If the
HTTP server is not listening during the load, llama-swap's health check sees
connection-refused until the model is up, and the **global**
`healthCheckTimeout` must cover the load. The generator currently writes 60.

We will make the global `healthCheckTimeout` an option
(`LlamaSwapOptions.health_check_timeout_s`, default 600) and raise the
generated value. The cost is stated: a llama-server model that fails to come
up is now reported after up to ten minutes instead of one. If the box turns
out to need both, the per-model `checkEndpoint` can be set to `none` for
llama-server models.

### 7. The image is an installable

The radiance image is installed the way every other llama-swap artefact is:
an installable on the Binaries page, with a tag picker and install progress.

It is a seed record of kind `docker_image` in
`genesis_worker/services/llama_swap/data/installables.yaml`:

```yaml
- kind: docker_image
  name: radiance
  label: Radiance engine (container)
  image: stilldeadcode/radiance
  default_tag: latest
```

The record carries no `variant` and no `backend`: the variant keys are the
llama-server binary variants (`cuda`, `cpu`, `vulkan`), and the radiance
container is not one of them. `primary` stays false — the llama-swap proxy
remains the service's primary installable.

The generated command runs the image that is installed (the recorded tag
wins over the default), so picking a tag on the Binaries page changes what
the next `docker run` pulls. The image is named in exactly one place — the
record — so the installed tag always wins and there is no second copy of the
image name in the service options.

One method on the service carries that:

```python
def radiance_image_ref(self) -> str | None:
    """The image ref the radiance command runs.

    The radiance ``docker_image`` record's repo, with the installed tag when
    one is recorded, else the record's default tag. None when the record is
    missing or disabled.
    """
```

It walks `installable_records()` for the enabled `docker_image` record named
`radiance` and composes `f"{record.image}:{installed_version or
record.default_tag}"`. `DockerImageInstall.image_ref` reports the *configured*
tag, not the installed one, so the method composes the ref itself from the
record plus `installed_version()`.

When the record is missing or disabled, the method returns `None`,
`BuildOptions.radiance` is `None`, and the generator skips radiance models:
turning the installable off turns radiance off. The config editor says so
instead of showing a model that cannot start.

### 8. The display

The config editor renders a radiance entry with its own fields, not the
llama-server ones: engine, image, container model path, host model path, GPU
index, resolved group GIDs, `tp`, `max_model_len`, `max_num_seqs`,
`kv_cache_dtype`, `gpu_headroom_mib`, `ttl`, `unloadTimeout`,
`checkEndpoint`, and the raw command.

The override form branches on the engine. The llama-server branch is
unchanged. The radiance branch offers the radiance knobs: max model len, max
num seqs, **TP**, **KV cache dtype**, GPU headroom MiB, extra radiance flags —
the last two positioned where the llama-server form has its kv-cache and
extra-flags widgets, so the two engines' forms line up side by side. The
radiance KV-cache widget is a selectbox over radiance's dtype vocabulary
(`bf16`, `fp8`, `fp8_v4`, `k6_v4`) rather than a free-text field, because the
guide restricts the last two to Qwen3.8-27B and Qwen3.8-Flash-Next; the
value stays hand-editable through the recipes overlay.

The pi export needs no change beyond the resolved context bound: the
radiance renderer writes `max_model_len` into `EvaluatedConfig.ctx_size`, so a
radiance model's `contextWindow` is its `--max-model-len`. One known gap: a
radiance model with a vision tower is exported as text-only, because the
catalog has no separate `mmproj` piece for a `.rad` container (the tower is
inside the container).

## Consequences

**Positive**

- One service, one config file, one proxy, one gateway. A radiance model
  appears in llama-swap's `/v1/models`, is routed by llama-swap's swap
  machinery, and is exported to pi-agent by the existing exporter. The
  Bifrost change the round-1 plan needed does not exist.
- The install path reuses `DockerImageInstall` and `DockerPullAcquireSession`
  unchanged: the image is pulled with progress, tagged, and recorded.
- The generator stays one pipeline. The engine is a field on the file set; the
  recipe cascade, the override store, the config editor and the exporter all
  keep their shape and learn one branch.
- The vocabulary stays honest: llama-server flags and radiance flags never mix
  in one command, because each renderer reads only its own layer.

**Negative**

- The generator grows a second renderer and the recipe schema grows a second
  block. The recipe vocabulary is no longer engine-agnostic, and the config
  editor needs a per-engine form.
- The generated command embeds host facts (the group GIDs, the GPU index).
  The config is regenerated per node, so this is correct, but a config
  generated on one node and copied to another carries the wrong numbers.
- The global `healthCheckTimeout` has to grow to cover a radiance load, which
  makes a failing llama-server load take longer to report.
- The radiance knobs are duplicated between the service options, the recipe
  block and the override block. That is the cost of the existing three-layer
  cascade, not a new invention.

**Neutral**

- Radiance still runs one model per container and one container per model
  entry; llama-swap starts it on demand and stops it per the TTL.
- The image is pulled by the worker's install path, not by the container
  runtime, so the first radiance start after a fresh install does not pay the
  pull.
- The t-9xp8ik dependency is settled: the install path uses the landed
  `docker_image` record kind and the landed seed file, and the only new
  surface is `radiance_image_ref()` plus one seed record.

## Open points, with a recommended default

1. **Global `healthCheckTimeout`.** Recommended: 600 s, as an option. The
   alternative is `checkEndpoint: none` on the radiance entry, which makes
   llama-swap declare the model ready before the container is listening.
2. **`ttl`.** Recommended: 0 (never unload), matching the current generator.
   The alternative is 600 s (Gen's reference `10m`), which frees the GPU when
   idle at the cost of a multi-minute reload on the next request.
3. **GPU index.** Recommended: an explicit `radiance.gpu_index` option,
   default 0, verified on the box (the R9700 is device 1 on the hybrid host).
4. **Group GIDs.** Recommended: resolve `render` and `video` with `getent` at
   generation time, omit a group the host does not have.

## Plan

`docs/arch/plans/plan-044-radiance-as-llama-swap-engine.md` — the approved
plan, in three phases: the engine concept and the radiance renderer, the knob
layers and the config editor, the seed records (the `docker_image` installable
and the radiance recipe) and the end-to-end tests.
