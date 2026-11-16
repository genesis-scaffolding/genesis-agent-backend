# Plan — speaches (TTS + STT) service for the Genesis Worker, with model-vault support

> **Approved.** Gen approved this plan (round 2) for task `t-q5ip1l` — *investigate
> + add the speaches service (TTS + STT) to genesis-agent-backend, with model-vault
> support*. The plan text below is the approved plan, kept here as the execution
> reference for ADR-047 and ADR-048.

Task: `t-q5ip1l` — *investigate + add the speaches service (TTS + STT) to genesis-agent-backend, with model-vault support*.
Worktree (write-scope): `/home/gentran/Documents/worktrees/t-q5ip1l-speaches/genesis-agent-backend`
Branch: `se/t-q5ip1l-speaches`.
ADRs: `docs/arch/adr-047-vault-backed-model-stores.md`,
`docs/arch/adr-048-speech-models-in-the-acquire-flow.md`.

---

# Plan (round 2) — speaches (TTS + STT) service for genesis-agent-backend, with model-vault support

Branch: `se/t-q5ip1l-speaches` (worktree `worktrees/t-q5ip1l-speaches/genesis-agent-backend`,
cut from `main` at `3fee2af`). Read-only reference checkout: `/home/gentran/Documents/genesis-agent-backend`.

Investigation + design only. Nothing is implemented, nothing is committed.

---

## 0. Round 2 changes

Two points from Gen's rejection (journal `j-9lvg36`) are folded in. Everything else from round 1
stands.

### 0.1 `ensure_writable_dir` on the vault's HF cache → **DROPPED**

**Verdict: drop the hook.** Evidence (all verified on this node and in the code):

| Question | Evidence | Answer |
|---|---|---|
| Is the chown needed for the container to write? | Worker uid/gid = **1000/1000** (`id -u` → `1000`). The speaches image's fixed user is **`ubuntu` uid 1000** (upstream `Dockerfile`: `useradd --create-home --shell /bin/bash --uid 1000 ubuntu`; `USER ubuntu`). The vault's HF cache on this node is `/home/gentran/models/huggingface/hub`, **mode 0755, owner `gentran:gentran` (1000:1000)** — and it is the *same inode* (343724) as `~/.cache/huggingface/hub`, i.e. the node's default HF cache already **is** the vault's. The container's uid 1000 is the **owner** → `rwx` → it can already write. | **No chown needed.** The hook would be a functional no-op. |
| What does the hook actually do here? | `hooks.py::_ensure_writable_dir` → `os.chown(target, 1000, 1000)`. Run as uid 1000 on a 1000:1000 dir, that **succeeds** (verified empirically: `os.chown(self,1000,1000) -> OK`) → the hook takes the `chowned=True` branch → `chmod 0o755`. | It **mutates shared vault state on every start** (a chown + chmod of the directory the framework's own HF source writes, the catalogue rescan reads, and other services read) to reach the state it is already in. |
| When does the 0777 fallback trigger? | Only when `os.chown` raises `OSError` — i.e. the worker is **neither root nor uid 1000** (an unprivileged process may only chown to itself), or the filesystem refuses. On this node: never. On any fleet node whose worker uid ≠ 1000: **the shared vault's HF cache is chmod'ed 0777**. | The fallback is exactly the loosening Gen objects to, and it would land on shared vault state. |
| Does the framework mutate the mount anyway? | `DockerService._ensure_volume_dirs()` mkdirs every volume's host path **and chowns it** — but only `if self._puid is not None and self._pgid is not None`, and those are `None` exactly when `puid_default`/`pgid_default` are `false` (`docker_service.py::__init__`). Our spec sets both `false`. | With `puid_default: false` the framework only **creates** a missing dir (as the host user) and never touches an existing dir's owner/mode. |
| What if the dir doesn't exist yet? | `_ensure_volume_dirs()` → `mkdir(parents=True, exist_ok=True)` as the host user (uid 1000 here) → created 0755, owned by 1000 → writable by the container. | Covered by the framework's existing mechanism — no hook needed. |

**Replacement (what the spec does instead):** nothing touches the vault's permissions.
`puid_default: false` / `pgid_default: false` stay (they also stop the framework injecting
`PUID`/`PGID` that the image ignores). The requirement — *the HF cache directory must be
writable by uid 1000* — is documented in the spec header and in the `hf_cache_dir` option's
`ui_help`, with the one-line remedy for a node whose worker uid differs:
`sudo chown -R 1000:1000 <hf_cache_dir>`.

**Failure mode if the requirement is not met** (documented, not papered over): the container still
starts and `/health` still answers 200 — only *downloads* fail, with a container-side
`PermissionError: [Errno 13] Permission denied: '/home/ubuntu/.cache/huggingface/hub/.locks/...'`
and a 500 from `POST /v1/models/{id}`. No silent permission change is made on shared state.

### 0.2 Acquire-flow extension → **IN SCOPE** (Gen's decision)

The round-1 "no framework change" constraint is lifted. `genesis_worker/sources/huggingface/acquire.py`
is extended so the model types speaches needs are acquirable through the Genesis UI:

1. **The model card is now fetched.** `README.md` is the metadata speaches derives a model from;
   without it the repo is invisible to speaches (`/v1/models` skips it, `/api/ps/{id}` → 500).
   - For a **speech bundle** the card is part of the bundle → its download failure is loud.
   - For **every other acquire** (GGUF / safetensors / rad) the card is fetched **best-effort**
     after the weights: a failure appends a warning to the acquire log tail and does **not** fail
     the acquire (the weights are already on disk).
2. **The classification is extended from single files to *layout-aware bundles*.** A speech model
   is only loadable when its files land in the cache **together** (Kokoro needs `voices.bin` next
   to `model.onnx`), so the selectable unit must be the bundle, not the file. New layout table:
   `kokoro` (`model.onnx` + `voices.bin`), `piper` (`model.onnx` + `config.json`),
   `ctranslate2` (`model.bin` + `config.json` + `tokenizer.json` + `vocabulary.*`,
   optional `preprocessor_config.json`), `parakeet` (`*.nemo`). The names mirror speaches' own
   `download_model_files` allow-patterns (upstream `src/speaches/executors/{kokoro,piper,whisper,parakeet}.py`).
   Gen's required minimum (Kokoro + CT2 + README) is covered; Piper and Parakeet are one table row
   each in the same mechanism.
3. **Files touched:** `sources/huggingface/acquire.py` (core), `sources/huggingface/ui/acquire.py`
   (the role filter that makes the bundle selectable), `sources/huggingface/__init__.py`
   (re-exports), `tests/test_acquire_hf.py` (new tests), `tests/test_generate_config.py`
   (the llama-swap filter test). **No contract change** (`contracts/acquire.py` is generic; the
   role is a plain string on the source's own `AcquireFileGroup`). **`contracts/classify.py` is
   deliberately untouched** (§2.7).

### 0.3 ADR decision → **two ADRs** (was: one)

Round 1 proposed one ADR (the vault-backed model store). Round 2 adds a **contract change on the
acquire side** (what is acquirable, the bundle as the selectable unit, the mandatory model card),
which is its own architectural decision with its own consequences. Following the repo's precedent
(ADR-025 *comfyui service* + ADR-026 *comfyui vault mount* — one ADR per decision, even inside one
feature):

* **`docs/arch/adr-047-vault-backed-model-stores.md`** — the service-side decision (unchanged from
  round 1): HF-cache-shaped services share the vault's HF cache by bind mount; no generic
  vault→service symlink applier; the vault becomes service-writable; the model card is the
  metadata contract.
* **`docs/arch/adr-048-speech-models-in-the-acquire-flow.md`** — the source-side decision (new):
  the acquire flow's selectable unit becomes a *layout bundle* for speech models; the model card
  is always fetched; the acquire contract now covers TTS/STT layouts.

### 0.4 Everything else that changed

| Item | Round 1 | Round 2 |
|---|---|---|
| `pre_start_hooks` in `speaches.yaml` | `ensure_writable_dir` on `$options.hf_cache_dir` | **removed** (§0.1) |
| `hf_cache_dir` `ui_help` | mentions the vault | additionally states the **uid-1000 write requirement** + the `chown` remedy |
| §4.6 "vault → speaches visibility" | required a zero-code `POST /v1/models` workaround | the Genesis UI can now acquire the model *with* its card (§4.3) |
| §7 risk 1 ("the acquire flow can't fetch TTS/STT weights") | open, deferred | **closed** by §0.2 |
| §7 risk 10 (0777 fallback on shared state) | open | **closed** (the hook is gone) |
| §6 QA | four gates + spec tests | four gates + spec tests + **acquire-flow tests** + **llama-swap filter test** |
| §8 ADR | one ADR | **two ADRs** (§8) |
| kind/docker verdict, bind-mount wiring, port 8001, `hf_cache_dir` default, spec shape otherwise | — | **unchanged** |

---

## 1. Goal

Add **speaches** (`https://github.com/speaches-ai/speaches`) — the OpenAI-API-compatible speech
server (TTS via Kokoro/Piper, STT via faster-whisper/Parakeet) — to the Genesis Worker, wired so that:

* the **model vault is the model store** (no second copy of the models, no speaches-private model
  directory),
* a model the user pulls through speaches (API/CLI) **persists in the vault** and survives
  container restarts,
* a model that is **already in the vault** is visible to speaches without speaches downloading
  anything,
* a TTS/STT model can be **acquired through the Genesis UI** (round-2 addition) and land in the
  vault complete — weights *and* model card — so speaches can serve it immediately.

---

## 2. Investigation findings

### 2.1 Upstream: how speaches stores and loads its models

**Since v0.8 speaches has no model directory of its own. Its model store *is* the HuggingFace hub
cache** (`HF_HUB_CACHE`, default `~/.cache/huggingface/hub`).

| Fact | Where upstream |
|---|---|
| The cache root is `huggingface_hub.constants.HF_HUB_CACHE`; the error messages name `HF_HUB_CACHE` | `src/speaches/hf_utils.py` |
| Local models are discovered by scanning the cache for `models--<org>--<repo>` dirs | `hf_utils.get_cached_model_repos_info` → `huggingface_hub.scan_cache_dir()`; `list_model_files()` globs `<repo>/snapshots/**/*` |
| Downloads go to the same cache | `WhisperModelRegistry.download_model_files` → `snapshot_download(..., allow_patterns=[...])` (no `cache_dir` ⇒ default cache); same for Kokoro/Piper/Parakeet |
| Model *metadata* is the **model card** (`README.md`) inside the cache snapshot — **no manifest / `model.json` / registry file** | `hf_utils.get_model_card_data_from_cached_repo_info` → `ModelCard.load(<snapshot>/README.md)`; the pre-v0.5 `~/.cache/speaches/<model>/model.json` scheme is long gone (v0.6/0.7 already used the HF cache) |
| A cache entry **without `README.md` is invisible** (`/v1/models` skips it; `/api/ps/{id}` → 500 `MODEL_CARD_DOESNT_EXISTS_ERROR_MESSAGE`, which blames the v0.8 breaking change) | `hf_utils.py`, `routers/utils.py::get_model_card_data_or_raise` |
| The card must also carry `library_name` (`onnx` / `ctranslate2` / `nemo`), `pipeline_tag` (`text-to-speech` / `automatic-speech-recognition`) and the `speaches`+`kokoro` / `speaches`+`piper` tags | `HfModelFilter.passes_filter`; verified live: `speaches-ai/Kokoro-82M-v1.0-ONNX` → `library_name=onnx`, `pipeline_tag=text-to-speech`, tags `['onnx','speaches','kokoro','text-to-speech',…]`, files `model.onnx, voices.bin, README.md`; `Systran/faster-whisper-tiny` → `library_name=ctranslate2`, files `config.json, model.bin, tokenizer.json, vocabulary.txt, README.md`; `speaches-ai/piper-en_US-amy-low` → files `config.json, model.onnx, README.md`; `nvidia/parakeet-tdt-0.6b-v2` → `parakeet-tdt-0.6b-v2.nemo, README.md` |
| A repo is usable only if installed locally: STT calls `get_model_card_data_or_raise(model)` first → 404 "Model is not installed locally. You can download the model using POST /v1/models" | `routers/stt.py`, `routers/utils.py` |
| Acquisition is `POST /v1/models/{model_id}` or `uvx speaches-cli model download {id}` | `routers/models.py::download_remote_model`, `executors/shared/registry.py::download_model_by_id` |
| The **Gradio web UI has no download button** in v0.8+ — it is a *playground* (the model dropdown comes from `client.models.list()`, local models only) | `ui/app.py`, `ui/tabs/{stt,tts,audio_chat}.py` |
| Deleting a model = `shutil.rmtree` on the cache repo dir | `hf_utils.delete_local_model_repo` (`DELETE /v1/models/{id}`) |
| The VAD model (silero) is **bundled in the image** — no download | `executors/silero_vad_v5.py` → `get_assets_path()/silero_encoder_v5.onnx` |
| Model aliases live in a **relative** `model_aliases.json` (→ the image WORKDIR), shipped in the image; `load_model_id_aliases()` raises if it is missing | `model_aliases.py`, repo root `model_aliases.json` |

### 2.2 Upstream: deployment shape

* Image **`ghcr.io/speaches-ai/speaches`**, tags `latest-cuda`, `latest-cpu`, `0.8.3-cuda`, … (verified
  live against GHCR: 100 tags). CUDA variants build on `nvidia/cuda:12.6.3-base-ubuntu24.04`.
* Fixed user **`ubuntu` uid 1000**, `HOME=/home/ubuntu`, `WORKDIR=/home/ubuntu/speaches`; the image
  pre-creates `/home/ubuntu/.cache/huggingface/hub`; `UVICORN_HOST=0.0.0.0`, `UVICORN_PORT=8000`;
  **ignores `PUID`/`PGID`**.
* `CMD ["uvicorn", "--factory", "speaches.main:create_app"]` → **container port 8000**.
* Health **`GET /health`** → `200 {"message":"OK"}`, unauthenticated (public router).
* Gradio playground at `/`; API under `/v1`.
* Upstream compose mounts an HF cache volume at `/home/ubuntu/.cache/huggingface/hub` — the shape we reuse.
* Env config (`pydantic-settings`): `ENABLE_UI`, `LOG_LEVEL`, `API_KEY`, `STT_MODEL_TTL`,
  `TTS_MODEL_TTL`, `WHISPER__INFERENCE_DEVICE`, `PRELOAD_MODELS`, …
* No auth by default; with `API_KEY` set, `/health`, `/docs`, `/openapi.json` and the **web UI stay public**.

### 2.3 The framework's model-vault mechanics

| Concern | Where |
|---|---|
| Vault root | `settings.py::PathsSettings.resolved_vault_path` (`GENESIS_PATHS__VAULT_PATH` → legacy `MODELS_ROOT` → `~/models`) |
| Vault root → plugins | `registries.py::ServiceRegistry._context_for_name` → `ServiceContext.vault_path` (ADR-023) |
| `$vault_path` placeholder | `utils/services/loader.py::_resolve_placeholder` |
| The vault's HF subtree | `sources/huggingface/source.py` → `vault_subdir = "huggingface/hub"`; overridable via `GENESIS_SOURCES__HUGGINGFACE__LOCAL_PATH` |
| The vault's HF layout | HF hub cache: `<vault>/huggingface/hub/models--<org>--<repo>/{blobs,refs,snapshots/<sha>}` — written by `huggingface_hub.hf_hub_download(..., cache_dir=<vault>/huggingface/hub)` in `sources/huggingface/acquire.py` |
| The catalogue | `catalog.py` + `utils/catalog_io.py` (persisted at `<state_dir>/catalog.json`) |
| ComfyUI's vault bridge | `services/comfyui/symlinks.py` (`SymlinkApplier`; yaml registry `<config_dir>/comfyui/model_symlink.yaml` → symlinks under `<vault>/comfyui/<role>/`, relative targets, ADR-026), mounted by `services/comfyui/lifecycle.py` (`/vault` ← vault root) |
| When ComfyUI's symlink step runs | **User-driven**: the ComfyUI *Models* page → "Add symlinks" → `SymlinkApplier.apply(catalog)`; "Prune dangling" → `prune_dangling()` |
| Why ComfyUI needs a bridge at all | ComfyUI scans a **flat, role-organised** dir; the vault is **source-organised**. The shapes differ ⇒ a bridge is required |
| Declarative DSL | `utils/services/{spec,loader,docker_service,hooks}.py` |
| Ports | `utils/ports/` — fixed ports reserved per service; auto range `18000-18999`; framework ports API 20985 / UI 8501 |

### 2.4 What a declarative YAML can and cannot express

Can: bind mounts with `$options.X` / `$vault_path` in the host path; arbitrary env with typed
option substitution and bool coercion; container↔host port split; health-probe path; web-UI path;
`--gpus all`; one pre-start hook from the existing registry.
Cannot: make the image or the GPU flags conditional on host hardware; express "materialise a
symlink farm from the catalogue" (no catalogue-aware hook kind exists); point speaches at anything
other than an HF cache (`HF_HUB_CACHE` is its only knob).

### 2.5 NEW — the vault's HF cache on this node (the Point-1 evidence)

```
$ stat -c '%n inode=%i mode=%a owner=%U:%G' ~/models/huggingface/hub ~/.cache/huggingface/hub
/home/gentran/models/huggingface/hub      inode=343724 mode=755 owner=gentran:gentran
/home/gentran/.cache/huggingface/hub      inode=343724 mode=755 owner=gentran:gentran
$ readlink -f ~/.cache/huggingface/hub
/home/gentran/models/huggingface/hub
```

* The vault's HF cache is `~/models/huggingface/hub` (0755, uid/gid 1000) — and the node's default
  `~/.cache/huggingface/hub` is the **same inode** (a symlink to it). The user's HF cache *is* the
  vault's HF cache.
* Contents today: `blobs/`, `.locks/`, `CACHEDIR.TAG`, and two GGUF repos
  (`models--peculiar-ragdoll--Tiel-Coder-35B-A3B-GGUF-MTP`, `models--ukisai--Swift-1.5-Qwen3.8-27B-GSQ-RCO-GGUF`).
* The speaches container's uid is 1000 → it is the **owner** of the mount target → `rwx` → it can
  write. No chown required (§0.1).
* The framework's `_ensure_volume_dirs()` creates the dir as the host user when missing and — with
  `puid_default: false` — performs **no chown** on it.

### 2.6 NEW — the acquire flow today (the code the extension builds on)

`genesis_worker/sources/huggingface/acquire.py`:

* `_inspect()` walks `HfApi.list_repo_tree(...)` and keeps only files ending in `.gguf`,
  `.safetensors`, `.rad`; everything else is dropped silently. The failure message is
  `"repository has no .gguf, .safetensors, or .rad files"`.
* `classify_path()` maps a path to a role (`main` / `mmproj` / `mtp` / `safetensor` / `radiance` /
  `unsupported`); `group_files()` groups GGUF shards into one selectable group.
* `_ROLE_ORDER = {"main": 0, "mmproj": 1, "mtp": 2, "safetensor": 0, "radiance": 0, "unsupported": 3}`.
* `_submit_select_files()` enforces: exactly one primary group, of role `main` / `safetensor` /
  `radiance`; `mmproj`/`mtp` are auxiliary; `radiance` must be alone. The error text is
  `"selected roles [...]; need exactly one 'main', 'safetensor', or 'radiance' group"`.
* `_run_inner()` downloads exactly the selected paths with
  `hf_hub_download(repo_id, filename, cache_dir, revision)` — one call per file. **`README.md` is
  never requested**, so an acquired repo has no model card.
* `sources/huggingface/ui/acquire.py` filters selectable groups with
  `g.role in ("main", "safetensor", "radiance")` — a new role must be added there or the group is
  invisible in the UI.
* `AcquireFileGroup` lives in the source package (not in `contracts/`) → a new role string needs no
  contract change. `contracts/acquire.py` is role-agnostic.

### 2.7 NEW — the llama-swap candidate filter (confirmed under the extended classification)

`services/llama_swap/generate_config.py::_is_llm_candidate` (l. 235-244):

```python
if source == "huggingface":
    return any(p.filename.lower().endswith((".gguf", ".rad")) for p in entry.pieces)
```

The filter is **extension-based**, not role-based. Under the extended classification the catalogue
pieces of an acquired speech repo are:

| Repo | Pieces (role) | Has `.gguf`/`.rad`? | LLM candidate? |
|---|---|---|---|
| Kokoro | `model.onnx` → *config* (`.onnx` ∉ `WEIGHT_EXTS`), `voices.bin` → *main* (`.bin` ∈ `WEIGHT_EXTS`), `README.md` skipped (`SKIP_FILENAMES`) | no | **no** |
| faster-whisper / CT2 | `model.bin` → *main*, `config.json`/`tokenizer.json`/`vocabulary.txt` → *config* | no | **no** |
| Piper | `model.onnx` → *config*, `config.json` → *config* → all pieces *config* → the HF source adds the note `"no model weights on disk"` | no | **no** (twice over: the note check *and* the extension check) |
| Parakeet | `*.nemo` → *config* → same as Piper | no | **no** |

`walk_models()` is the single filter for `build_config` (config.yaml), `evaluate_all` /
`evaluate_all_report` (the recipes UI) and `export_pi_config.build_provider_for_catalog` (the
pi-models export) → **TTS/STT entries cannot reach the LLM config, the recipes UI or the pi
provider**. A regression test is added (§6.3).

`contracts/classify.py` is deliberately **not** changed: the LLM filter is extension-based, so
relabelling `.onnx`/`.nemo` pieces would change nothing functionally, while the shared classifier
also feeds ComfyUI's role dropdown (`services/comfyui/ui/models.py`) — a wider blast radius than
the task needs. Two cosmetic artefacts are accepted and recorded in §7.

---

## 3. The chosen approach (round 1 verdict stands)

### Verdict: **(a) pure declarative YAML**, with the vault's HF cache bind-mounted as the container's `HF_HUB_CACHE`, plus a **source-side acquire-flow extension** (round 2).

1. **speaches' model store is an HF hub cache** (§2.1) and the **vault's HF source already produces exactly that layout** (§2.3). The vault *is* a valid speaches cache — the integration is a bind mount, not a translation.
2. **A symlink farm cannot work.** ComfyUI's bridge exists because ComfyUI wants a flat, role-organised directory. speaches reads `HF_HUB_CACHE` and expects `models--<org>--<repo>/{blobs,refs,snapshots/<sha>}` — it calls `huggingface_hub.scan_cache_dir()` and `ModelCard.load(<snapshot>/README.md)`. A flat `<vault>/speaches/<role>/<file>` farm is *unreadable* to it. Faking one would mean hand-building `refs/`, `blobs/` and `snapshots/<sha>/` around symlinks — re-implementing the HF cache layout, strictly worse than sharing the real one, and still not carrying the mandatory model card.
3. **A Python service buys nothing here** beyond conditional GPU/image selection (§2.4) — a separate concern.
4. **It satisfies "persist what the user downloads" more strongly than the alternatives**: the container's cache *is* the vault's cache, so anything speaches downloads lands in the vault — no second store, no manifest, no copy to reconcile.
5. **The acquire extension closes the last gap** (§0.2): the vault can now be filled with a *complete* speech model (weights + card) through the Genesis UI.

Rejected, explicitly: **(b)** a YAML + new pre-start hook that materialises vault→speaches symlinks
(nothing for speaches to scan but an HF cache; the hook would have to fabricate one) and
**(c)** a custom Python service mimicking ComfyUI's symlink lifecycle (copies a mechanism whose
premise does not hold here).

**What we deliberately do not generalise:** the ComfyUI symlink mechanism stays ComfyUI-specific
(ADR-047).

---

## 4. The design

### 4.1 The service spec (round 2 — the `pre_start_hooks` block is gone)

```yaml
# Speaches declarative service spec — OpenAI-compatible TTS + STT server.
#
# MODEL STORE — the model vault's HuggingFace cache, shared
# --------------------------------------------------
# speaches has no model directory of its own: since v0.8 it stores every
# model as a HuggingFace hub-cache repo (``models--org--repo``) under
# ``HF_HUB_CACHE`` (upstream: src/speaches/hf_utils.py, src/speaches/executors/*).
# The Genesis model vault keeps its HuggingFace models in exactly that
# layout (``<vault>/huggingface/hub`` — sources/huggingface), so the vault IS
# a valid speaches cache. We bind-mount it at the image's default cache path
# and point ``HF_HUB_CACHE`` at it:
#
#   * a model pulled with ``POST /v1/models/{id}`` (or
#     ``uvx speaches-cli model download {id}``) lands in the VAULT and
#     survives container restarts;
#   * a model acquired through the Genesis UI (with its README.md model card)
#     is visible to speaches as soon as it lands.
#
# The ComfyUI-style symlink farm is deliberately NOT used: ComfyUI needs a
# flat, role-organised directory, while speaches can only read an HF hub
# cache. See docs/arch/adr-047-vault-backed-model-stores.md.
#
# PERMISSIONS — the mount target is shared vault state and is NOT chmod'ed or
# chowned by this service. The image runs as a fixed ``ubuntu`` (uid 1000),
# so the HF cache directory must be writable by uid 1000. On this node it is
# (the vault's cache is 0755 gentran:gentran, and the node's default
# ~/.cache/huggingface/hub is the same inode). On a node whose worker uid
# differs, chown it once: ``sudo chown -R 1000:1000 <hf_cache_dir>``.
# ``puid_default``/``pgid_default`` are off so the framework neither injects
# PUID/PGID (the image ignores them) nor chowns the mount target.

version: 1
kind: docker
name: speaches
display_name: Speaches
description: OpenAI-compatible TTS and STT server
category: media

capabilities:
  can_generate_config: false
  can_export_for_agent: false
  can_serve_llm: false
  can_serve_image: false
  can_train_models: false
  has_web_ui: true
  can_install: true

resource_estimate:
  vram_bytes_typical: 4000000000
  vram_bytes_min: 1000000000
  cpu_cores_recommended: 2

image:
  repo: ghcr.io/speaches-ai/speaches
  tag: latest-cuda
  install_name: speaches
  source_url: https://github.com/speaches-ai/speaches

container:
  name: speaches
  listen_host: 0.0.0.0
  listen_port: $options.listen_port
  internal_port: 8000        # uvicorn inside the image
  health_probe_path: /health # public + unauthenticated (upstream routers/misc.py)
  web_ui_path: /             # the Gradio playground
  restart_policy: unless-stopped
  puid_default: false        # the image runs as a fixed `ubuntu` (uid 1000)
  pgid_default: false
  gpu_flags: ["all"]

env:
  HF_HUB_CACHE: "/home/ubuntu/.cache/huggingface/hub"
  ENABLE_UI: "$options.enable_ui"
  LOG_LEVEL: "$options.log_level"

volumes:
  /home/ubuntu/.cache/huggingface/hub: "$options.hf_cache_dir"

options:
  listen_port:
    type: port
    default: 8001
    ui_label: Web UI / API port
    ui_help: Host-side port for the speaches web UI and the OpenAI-compatible API
    ui_group: Network
  hf_cache_dir:
    type: path
    default: "$vault_path/huggingface/hub"
    ui_label: Model cache directory
    ui_help: The HuggingFace cache speaches reads and writes. Keep it on the model vault's HF cache (<vault>/huggingface/hub) so models are shared with the rest of the fleet. If you moved the HF cache with GENESIS_SOURCES__HUGGINGFACE__LOCAL_PATH, point this at the same directory. It must be writable by uid 1000 (the image's fixed user).
    ui_group: Storage
  enable_ui:
    type: bool
    default: true
    ui_label: Enable web UI
    ui_help: Serve the Gradio playground. Turn it off to save memory and startup time; the API is unaffected.
    ui_group: Advanced
  log_level:
    type: string
    default: "info"
    ui_label: Log level
    ui_group: Advanced
  extra_env:
    type: env_map
    ui_label: Extra environment variables
    ui_help: Additional environment variables (e.g. API_KEY to require an API key, STT_MODEL_TTL)
    ui_group: Advanced
  extra_mounts:
    type: mount_map
    ui_label: Extra volume mounts
    ui_help: Additional host to container bind mounts
    ui_group: Advanced
```

Everything else (identity, image, ports, probe, options) is round 1, unchanged.

**The model-vault wiring step** is now just the bind mount + the env var:

```yaml
env:
  HF_HUB_CACHE: "/home/ubuntu/.cache/huggingface/hub"
volumes:
  /home/ubuntu/.cache/huggingface/hub: "$options.hf_cache_dir"
```

At `start()` the framework pre-creates the host path if it is missing (as the host user, no
chown) and then runs `docker run … -v <vault>/huggingface/hub:/home/ubuntu/.cache/huggingface/hub`.
**No hook, no permission change.**

Directory layout — vault vs. what speaches expects (they are the same):

```
<vault>/                                   (settings.paths.resolved_vault_path)
└── huggingface/hub/                       <- $options.hf_cache_dir  ==  $HF_HUB_CACHE in the container
    ├── .locks/<org>--<repo>/<file>.lock
    └── models--<org>--<repo>/             <- one repo == one speaches model id ("<org>/<repo>")
        ├── blobs/<etag>                   (content-addressed, mode 0444)
        ├── refs/main -> <commit-sha>
        └── snapshots/<commit-sha>/
            ├── README.md -> ../../blobs/<etag>    (the MODEL CARD — mandatory metadata)
            ├── model.onnx -> ../../blobs/<etag>    (Kokoro)
            ├── voices.bin -> ../../blobs/<etag>    (Kokoro voices)
            ├── config.json, tokenizer.json, model.bin, vocabulary.txt   (faster-whisper / CT2)
```

vs. the ComfyUI-only shape that is **not** used here:

```
<vault>/comfyui/<role>/<file> -> ../../huggingface/hub/...     # ADR-026 symlink farm — ComfyUI only
```

### 4.2 The acquire-flow extension (round 2) — full design

**File: `genesis_worker/sources/huggingface/acquire.py`**

1. **A layout table** (the extension of the file classification):

```python
@dataclass(frozen=True)
class SpeechLayout:
    """One file layout of a speech model speaches can serve.

    ``required`` patterns must ALL be present for the layout to match;
    ``optional`` patterns are fetched when present. The names mirror
    speaches' own ``download_model_files`` allow-patterns (upstream
    ``src/speaches/executors/{kokoro,piper,whisper,parakeet}.py``).
    """
    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()

SPEECH_LAYOUTS: tuple[SpeechLayout, ...] = (
    SpeechLayout("kokoro", ("model.onnx", "voices.bin")),
    SpeechLayout("piper", ("model.onnx", "config.json")),
    SpeechLayout(
        "ctranslate2",
        ("model.bin", "config.json", "tokenizer.json", "vocabulary.*"),
        ("preprocessor_config.json",),
    ),
    SpeechLayout("parakeet", ("*.nemo",)),
)

MODEL_CARD_FILENAME = "README.md"
```

Patterns are matched with `fnmatch` against the **basename**; the most specific layout wins
(order above). Gen's required minimum (Kokoro + CT2) is covered; Piper and Parakeet are one row
each in the same mechanism.

2. **A bundle builder** — the selectable unit for speech models:

```python
def speech_groups(files: list[_RemoteFile]) -> list[AcquireFileGroup]:
    """One group per complete speech-model layout found in the repo.

    A speech model is only loadable when its files land in the cache
    together (Kokoro needs ``voices.bin`` next to ``model.onnx``), so the
    selectable unit is the bundle, not the file. Files are grouped by
    directory first: a bundle's files must live in the same directory.
    The model card is part of the bundle — speaches cannot list a repo
    without it.
    """
```

Behaviour:
* group the repo's files by parent directory;
* within a directory, test each layout: every `required` pattern must match ≥ 1 file;
* the bundle's `paths` = the matched weight files (layout order) + the matched optional files +
  **`README.md`** (the card is mandatory — a directory without it yields no bundle);
* `role="speech"`, `is_sharded=False`, `size` = the sum of the files,
  `label = f"speaches: {layout.name} ({n} files)"`.

3. **`_inspect()`** — keep the GGUF / safetensors / rad handling exactly as-is and append the
   speech groups:

```python
self._groups = group_files(self._files)          # GGUF (unchanged)
self._groups += [safetensors..., rads...]         # (unchanged)
self._groups += speech_groups(all_files)          # NEW
```

and the failure message becomes
`"repository has no .gguf, .safetensors, .rad, or complete speech-model files (a speech model needs its files together plus README.md)"`.

`_ROLE_ORDER` gains `"speech": 0` so the bundle sorts with the other primaries.

4. **`_submit_select_files()`** — the validation gains a speech branch:

* a speech bundle is a valid **primary** (exactly one);
* it may **not** be mixed with another primary (`safetensor` + `speech` → error);
* it may **not** carry aux groups (`mmproj` / `mtp` are meaningless for a speech model → error);
* the error text keeps the existing shape:
  `"selected roles [...]; need exactly one 'main', 'safetensor', 'radiance', or 'speech' group"`.

5. **`_run_inner()`** — the model card:

* the card is **already in the bundle's paths** for a speech acquire → it is downloaded with the
  weights, and a failure fails the acquire loudly (a card-less speech model is useless to speaches);
* for **every other acquire** the card is fetched **after** the weights, best-effort:

```python
if MODEL_CARD_FILENAME not in selected_paths:
    try:
        with _capture_stderr() as buf:
            download(repo_id=..., filename=MODEL_CARD_FILENAME,
                     cache_dir=str(self._cache_dir), revision=self._revision)
        self._append_log("Fetched model card (README.md)")
    except Exception as exc:  # noqa: BLE001 — the weights are already on disk
        self._append_log(f"Model card not fetched: {exc}")
```

The card's bytes are not added to the progress counters (they would overshoot `bytes_total`).

**File: `genesis_worker/sources/huggingface/ui/acquire.py`**

* `main_groups` filter gains `"speech"`:
  `g.role in ("main", "safetensor", "radiance", "speech")`;
* the label already reads well: `speaches: kokoro (2 files)  (model.onnx)` (the bundle's first
  path is a weight file, the card is last).

**File: `genesis_worker/sources/huggingface/__init__.py`** — re-export the new public names
(`SPEECH_LAYOUTS`, `SpeechLayout`, `speech_groups`) alongside `classify_path` / `group_files`,
consistent with the package's current re-export style.

**Not touched:** `contracts/acquire.py` (role-agnostic), `contracts/classify.py` (§2.7),
`sources/huggingface/source.py` (the walker already sees any file in the snapshot).

### 4.3 End-to-end: acquire → vault → speaches

1. The user opens the HF source's **Acquire model** page and types
   `speaches-ai/Kokoro-82M-v1.0-ONNX`.
2. `_inspect()` sees `README.md`, `model.onnx`, `voices.bin` → the `kokoro` layout matches → one
   group `speaches: kokoro (2 files)`, role `speech` → the UI offers it in "Model(s) to download".
3. Confirm → `_run_inner()` downloads `model.onnx`, `voices.bin`, `README.md` into
   `<vault>/huggingface/hub/models--speaches-ai--Kokoro-82M-v1.0-ONNX/`.
4. The next catalogue rescan sees the repo (pieces `model.onnx` → *config*, `voices.bin` → *main*;
   `README.md` skipped) — and **llama-swap ignores it** (no `.gguf`/`.rad` piece, §2.7).
5. speaches (running with the vault's HF cache bind-mounted) now lists the model at
   `GET /v1/models` — the card is what made it visible — and serves it:
   `POST /v1/audio/speech {"model": "speaches-ai/Kokoro-82M-v1.0-ONNX", "voice": "af_heart", "input": "…"}`.
6. The same model is visible to every other service that reads the vault's HF cache.

The reverse direction still holds: `POST /v1/models/{id}` (or the CLI) downloads into the vault
cache, so speaches-initiated downloads are persisted in the vault too.

### 4.4 Traefik / domain interaction (unchanged from round 1)

* The spec declares **no TLS** — the container speaks plain HTTP; TLS is terminated by the cluster
  ingress (the `traefik` compose service owns the wildcard certificate). Nothing about TLS belongs
  in the speaches spec.
* The orchestrator pushes the route table (`materialize_orchestrator_config`, `key: routes`);
  a route is `speaches-<node>.<domain>` → `http://<node>:8001`. The Gradio UI (`/`) and the API
  (`/v1`) are path-agnostic → one hostname serves both; Gradio's websocket/SSE streaming works
  through traefik.
* The framework probe hits `http://127.0.0.1:8001/health` (plain HTTP on the host port).

---

## 5. Files / areas touched

**New**

| Path | What |
|---|---|
| `genesis_worker/services/_declarative/speaches.yaml` | the spec (§4.1) |
| `docs/arch/adr-047-vault-backed-model-stores.md` | ADR — the service-side decision (§8) |
| `docs/arch/adr-048-speech-models-in-the-acquire-flow.md` | ADR — the source-side decision (§8) |
| `docs/arch/plans/plan-047-speaches-service.md` | optional: this plan copied into the repo (both ADRs link to it) |

**Modified — the service**

| Path | What |
|---|---|
| `genesis_worker/tests/test_declarative_specs.py` | add `"speaches.yaml"` to `_BUILT_IN_SPECS`; special-case speaches in the shared VRAM assertion (like the existing bifrost branch); add the speaches-specific tests (§6.2) |
| `genesis_worker/tests/test_services_registry.py` | add `"speaches"` to the expected set in `test_registry_all_returns_every_service` |

**Modified — the acquire flow (round 2)**

| Path | What |
|---|---|
| `genesis_worker/sources/huggingface/acquire.py` | `SpeechLayout` + `SPEECH_LAYOUTS` + `MODEL_CARD_FILENAME`; `speech_groups()`; `_inspect()` integration + new failure message; `_ROLE_ORDER["speech"]`; `_submit_select_files()` speech rules; `_run_inner()` model-card fetch (mandatory inside a bundle, best-effort otherwise) |
| `genesis_worker/sources/huggingface/ui/acquire.py` | add `"speech"` to the selectable-role filter |
| `genesis_worker/sources/huggingface/__init__.py` | re-export the new public names |
| `genesis_worker/tests/test_acquire_hf.py` | new acquire tests (§6.3) |
| `genesis_worker/tests/test_generate_config.py` | the llama-swap filter regression test (§6.3) |

**Not touched** (deliberately)

* `genesis_worker/utils/services/hooks.py` — **no new hook kind**; the `ensure_writable_dir` hook is
  not used by this service (and stays as-is for prometheus/traefik).
* `genesis_worker/utils/services/{loader,spec,docker_service}.py` — no declarative-framework change.
* `genesis_worker/contracts/{acquire,classify}.py` — role-agnostic / deliberate (§2.7).
* `genesis_worker/services/` (Python) — no plugin package (YAML is invisible to
  `test_plugin_boundary.py`; `services/_declarative/__init__.py` stays import-free).
* `README.md` — its "Services" table lists only the three Python services; the declarative ones are
  not listed either.

---

## 6. QA plan

### 6.1 The four gates (AGENTS.md) — all must pass

```bash
uv run ruff check --fix genesis_worker     # auto-fixers first
uv run ruff format genesis_worker
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
uv run ruff format --check genesis_worker
```

(`make test` is the thin wrapper; the four invocations are the gate.)

### 6.2 Service-spec tests — `genesis_worker/tests/test_declarative_specs.py` (hermetic)

1. **`test_speaches_identity`** — name / display_name / `category.value == "media"` / description,
   `image_ref == "ghcr.io/speaches-ai/speaches:latest-cuda"`, `config.listen_port == 8001`,
   `config.internal_port == 8000`, `config.health_probe_path == "/health"`,
   `config.web_ui_path == "/"`, `config.gpu_flags == ["all"]`,
   `config.puid_default is False and config.pgid_default is False`.
2. **`test_speaches_binds_the_vault_hf_cache`** — `service_ctx(tmp_path, name="speaches")`;
   `svc.config.extra_volumes["/home/ubuntu/.cache/huggingface/hub"] == str(ctx.vault_path / "huggingface" / "hub")`
   and `svc.config.extra_env["HF_HUB_CACHE"] == "/home/ubuntu/.cache/huggingface/hub"`. Then build a
   fake HF cache entry in the vault
   (`<vault>/huggingface/hub/models--speaches-ai--Kokoro-82M-v1.0-ONNX/snapshots/<sha>/{README.md,model.onnx}`),
   assert the Genesis `HuggingFaceSource` discovers it as a catalogue entry **and** that the speaches
   volume points at that same directory — "what is in the vault is what speaches reads", without
   docker or network.
3. **`test_speaches_declares_no_pre_start_hooks`** — `svc.config.pre_start_hooks == ()`: the service
   must not mutate the vault's permissions (the round-2 verdict).
4. **`test_speaches_hf_cache_dir_option_overrides_the_vault`** —
   `service_ctx(..., options={"hf_cache_dir": str(tmp_path / "elsewhere")})` → the volume's host path
   becomes that directory.
5. **`test_speaches_options_have_configure_panel`** — `"configure" in svc.ui_panels`; defaults
   `listen_port == 8001`, `enable_ui is True`, `log_level == "info"`, `extra_env == {}`,
   `extra_mounts == {}`, `hf_cache_dir` → the vault path.
6. Relax the shared VRAM assertion in `test_built_in_spec_capabilities_and_resource` for
   `speaches.yaml` (mirroring the bifrost branch) and assert the exact numbers in
   `test_speaches_identity`.

### 6.3 Acquire-flow tests — `genesis_worker/tests/test_acquire_hf.py` (hermetic, no network)

Using the existing `_make_api([...])` / fake-`hf_hub_download` helpers:

1. **`test_kokoro_repo_surfaces_one_speech_bundle`** — tree `README.md`, `model.onnx`, `voices.bin`
   → one group, `role == "speech"`, `paths == ["model.onnx", "voices.bin", "README.md"]`.
2. **`test_ctranslate2_repo_surfaces_one_speech_bundle`** — tree `README.md`, `config.json`,
   `model.bin`, `tokenizer.json`, `vocabulary.txt` → one group with all five paths.
3. **`test_speech_bundle_without_the_model_card_is_not_offered`** — tree `model.onnx`,
   `voices.bin` (no README.md) → no speech group; the inspection fails with the new message.
4. **`test_speech_bundle_downloads_every_file_including_the_card`** — select the bundle, confirm →
   the fake download records one call per path, `README.md` included.
5. **`test_speech_bundle_cannot_be_mixed_with_another_primary`** — select a speech group + a GGUF
   group → the session stays in SELECTING with an error.
6. **`test_speech_bundle_rejects_auxiliary_files`** — a speech primary + an `mmproj` aux → error.
7. **`test_model_card_is_fetched_for_a_gguf_acquire`** — a GGUF acquire records an extra
   `hf_hub_download` call for `README.md`.
8. **`test_model_card_failure_does_not_fail_the_acquire`** — the fake download raises for
   `README.md` → the session still reaches COMPLETE and the log tail mentions the card.
9. **`test_repo_without_supported_files_reports_the_new_formats`** — the inspection failure message
   mentions `.onnx` / speech models.

### 6.4 Llama-swap filter regression — `genesis_worker/tests/test_generate_config.py`

10. **`test_evaluate_all_filters_out_speech_models`** — mirror
    `test_evaluate_all_filters_out_image_models`: a catalogue with a `_gguf_entry(...)` **plus**
    Kokoro-shaped and CT2-shaped entries → `evaluate_all` yields only the GGUF entry. A
    `_speech_entry(name, filenames)` helper builds the speech entries with the roles the walker
    would produce (`model.onnx` → *config*, `voices.bin` → *main*, …).

### 6.5 Manual smoke (not part of the gate; needs docker + network)

```bash
curl -s http://127.0.0.1:8001/health
curl -s http://127.0.0.1:8001/v1/models
curl -sX POST http://127.0.0.1:8001/v1/models/speaches-ai/Kokoro-82M-v1.0-ONNX
ls ~/models/huggingface/hub/                       # the repo is now in the vault
curl -s http://127.0.0.1:8001/v1/models            # the model is listed
# and via the Genesis UI: acquire "speaches-ai/Kokoro-82M-v1.0-ONNX" → the bundle appears
docker exec speaches ls -l /home/ubuntu/.cache/huggingface/hub
```

---

## 7. Risks

**Service side** (round 1, with the permissions risk removed)

1. **GPU.** `gpu_flags: ["all"]` needs an NVIDIA GPU + the nvidia container toolkit (CDI on Docker
   29 — this node qualifies: RTX 4060 Ti, Docker 29.9.2). On a **CPU-only node the container fails
   to start** ("could not select device driver"), and the declarative DSL cannot switch image/GPU
   flags by host hardware the way ComfyUI's Python service does. Fix if needed: a one-line YAML
   change (`tag: latest-cpu`, drop `gpu_flags`). The CUDA image also pins a CUDA runtime (12.6.3)
   ⇒ the host driver must be new enough; older-driver nodes pick `latest-cuda-12.4.1` in the Images
   page.
2. **The container can write to — and delete from — the vault.** `POST /v1/models/{id}` fills the
   vault's disk; `DELETE /v1/models/{id}` `rmtree`s the vault's cache repo (and the catalogue entry
   with it). The API is unauthenticated by default (`API_KEY` unset) → anything that can reach the
   port (or the traefik route) can do this. Mitigation: keep the port off the public internet, or
   set `API_KEY` via `extra_env`.
3. **Port / domain / traefik.** Host port **8001** must be free on the node (it is here; the
   allocator's fixed-port registry keeps `$auto_port` services off it). No TLS in the spec — the
   ingress terminates it. `model_aliases.json` stays the image's default (we do not mount over it: a
   missing host file would be created by docker as a *directory* and break
   `load_model_id_aliases()`; a `seed_json_file` hook kind would be the clean fix — follow-up).
4. **Shared-cache side effects.** The catalogue lists speaches-downloaded and Genesis-acquired
   speech repos. Harmless for llama-swap (extension-based filter, §2.7) but new noise on the Models
   page. Conversely `worker.delete_model(...)` can remove a model speaches is serving.
5. **Scan cost.** speaches lists local models with `huggingface_hub.scan_cache_dir()` over the whole
   cache (upstream calls the alternative "much slower") → `/v1/models` can take seconds on a large
   vault; it also parses every `README.md` in the cache.
6. **Upstream drift.** The spec pins `latest-cuda`; the Images page records a user pick. The
   model-storage contract has been stable since v0.6, so the wiring survives tag changes.

**Acquire side** (round 2)

7. **The layout table is a copy of speaches' allow-patterns.** A new upstream speech layout is not
   acquirable until the table gains a row. Mitigation: the table is four rows and carries the
   upstream source pointers in its docstring; the fallback is always `POST /v1/models/{id}` (which
   still lands in the vault).
8. **Monorepo layouts are not matched.** The matcher groups files by directory and matches by file
   *name*, so the flat speaches-compatible repos (Kokoro, CT2, Piper) work, but a monorepo of
   Piper voices with per-voice file names (`rhasspy/piper-voices`: `<voice>.onnx` + `<voice>.onnx.json`)
   yields no bundle. Mitigation: the speaches-compatible Piper repos (`speaches-ai/piper-*`) are flat
   and do match.
9. **The model-card fetch adds one request per acquire** and can fail (best-effort for non-speech
   acquires — a warning in the log tail; mandatory for speech bundles — a loud failure). The card is
   ~2 KB.
10. **Cosmetic catalogue artefacts** (accepted, `contracts/classify.py` untouched): `voices.bin` /
    `model.bin` are labelled role *main*; Piper and Parakeet entries carry the note "no model
    weights on disk" because every one of their pieces classifies as *config*. Neither affects the
    LLM config (§2.7). Follow-up if the noise matters: extend `WEIGHT_EXTS` with `.onnx` / `.nemo`
    (and re-check ComfyUI's role dropdown).
11. **The acquire UI is a Streamlit page** (never imported by pytest, per
    `docs/test-architecture.md`) → the `"speech"` role-filter change is not unit-tested; the logic
    behind it is (the group roles are asserted in §6.3).
12. **Permissions on the vault are no longer adjusted** (round-2 decision). If the worker uid ever
    differs from 1000, downloads fail with a container-side `PermissionError` until the operator
    chowns the cache dir once. This is a deliberate trade: a documented one-time command beats a
    silent chmod on shared vault state on every start.

---

## 8. ADR decision — **two ADRs**

**ADR-047 — `docs/arch/adr-047-vault-backed-model-stores.md`** (unchanged from round 1's §8):

* **Title** — *Vault-backed model stores for HuggingFace-cache-shaped services*.
* **Context** — the vault is source-organised; ComfyUI needs a flat role-organised dir (ADR-026's
  symlink mount); speaches ≥0.8 stores models as HF hub-cache repos and can only be pointed at
  `HF_HUB_CACHE`; the vault's HF source already produces that layout. Alternatives: (a) share the
  vault's HF cache, (b) a per-service cache seeded by a symlink farm, (c) a Python service with a
  bespoke applier.
* **Decision** — "We will point HuggingFace-cache-shaped services at the model vault's HuggingFace
  cache by bind-mounting it into the container and setting `HF_HUB_CACHE`. We will not build a
  generic vault→service symlink applier; the ComfyUI symlink farm stays ComfyUI-specific. The
  vault becomes writable by service containers: a service may download into it and delete from it."
* **Status** — Proposed.
* **Consequences** — positive (one store, no duplication, downloads persist in the vault, no new
  framework code); negative (a service can delete vault content; the container's uid must be able to
  write the vault cache — **no permission is changed automatically**, the operator does that once);
  neutral (the ComfyUI symlink farm is unchanged; the declarative DSL still cannot switch image/GPU
  by hardware).

**ADR-048 — `docs/arch/adr-048-speech-models-in-the-acquire-flow.md`** (new, round 2):

* **Title** — *Speech models in the HuggingFace acquire flow*.
* **Context** — the acquire flow's contract is "one selectable model = one weight file (+ shards)",
  which fits GGUF and safetensors but not speech models: a Kokoro model is `model.onnx` +
  `voices.bin`, a CT2 model is `model.bin` + `config.json` + `tokenizer.json` + `vocabulary.txt`,
  and speaches additionally requires the `README.md` model card before it will list a repo at all.
  The acquire flow neither surfaced those files nor fetched the card, so the vault could not hold a
  model that speaches can serve. Alternatives: (a) keep the flow LLM-shaped and let speaches
  download its own models; (b) extend the flow with per-file roles and let the user pick the
  companions by hand; (c) extend the flow with **layout bundles** + a mandatory model card.
* **Decision** — "We will extend the HuggingFace acquire flow with **layout-aware bundles**: a
  speech model is surfaced and downloaded as one group whose file set is derived from a small table
  of known layouts (kokoro, piper, ctranslate2, parakeet). The model card (`README.md`) is part of
  a speech bundle and is fetched best-effort for every other acquire. We will not let the user
  assemble a speech model from individually selectable files."
* **Status** — Proposed.
* **Consequences** — positive (TTS/STT models become acquirable through the Genesis UI and land in
  the vault complete, which is what makes ADR-047's "the vault is the store" true for speech too;
  the card is available to any future card-based consumer); negative (the acquire contract gains a
  second shape — a bundle — and the layout table must track upstream's allow-patterns; a repo whose
  layout is unknown is still not acquirable); neutral (the GGUF/safetensors/rad paths and the
  existing tests are untouched; `contracts/classify.py` is unchanged).
* **Plan** — link to `docs/arch/plans/plan-047-speaches-service.md`.

Both ADRs are written **before** the code (AGENTS.md flow), and nothing is committed until the
user has reviewed and approved.


**task:** t-q5ip1l
