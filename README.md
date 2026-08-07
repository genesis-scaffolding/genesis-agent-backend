# my-agent-backend

A toolkit for managing GGUF models behind [llama-swap](https://github.com/mostlygeek/llama-swap) on a single Linux + NVIDIA box.

## Status

**Work in progress.** Works on my setup; expect rough edges. APIs and file layout will change.

## What it does

Walks a local models directory, builds a YAML catalog, and uses a small recipe file to generate a llama-swap config. Adding a new model usually means dropping the GGUF on disk and adding a few lines to `recipes.yaml`.

| File | Role |
|---|---|
| `bin/catalog.py` | scans `<MODELS_ROOT>/huggingface/hub/` and `<MODELS_ROOT>/lmstudio/models/`, emits `MODEL_CATALOG.yaml` and `MODEL_CATALOG.md` |
| `bin/hf-model.py` | interactively selects GGUF quantizations and auxiliary files from a Hugging Face repository, then downloads them into the HF cache |
| `bin/pi-models.py` | generates a pi-agent `models.json` from `config.yaml` |
| `bin/build-config.py` | reads the catalog + `recipes.yaml`, writes `config.yaml` (one llama-swap entry per model) |
| `recipes.yaml` | curated family-level profiles (sampling recipe, chat template, MTP, resource knobs) |
| `bin/up` | wraps `llama-swap` in a tmux session, watches `config.yaml` |
| `bin/bonsai-server` | direct `llama-server` invocation for Bonsai (debugging without llama-swap) |
| `Makefile` | orchestrates the above |

## Quick start

```sh
# 1. Clone the llama.cpp forks into vendor/
git clone https://github.com/ggerganov/llama.cpp.git vendor/llama.cpp
git clone https://github.com/PrismML-Eng/llama.cpp.git vendor/prism-llama.cpp

# 2. Build them with CUDA
cmake -B vendor/llama.cpp/build -DGGML_CUDA=ON
cmake --build vendor/llama.cpp/build -j
cmake -B vendor/prism-llama.cpp/build -DGGML_CUDA=ON
cmake --build vendor/prism-llama.cpp/build -j

# 3. Point at your models dir
cp .env.example .env
$EDITOR .env       # set MODELS_ROOT=/path/to/your/models

# 4. Build catalog + config (writes config.yaml directly)
make all

# 5. Start llama-swap
make up
```

`make up` boots llama-swap in a tmux session named `swap`, listening on `0.0.0.0:8080` by default. Override with `LISTEN=127.0.0.1:8080 make up`.

## Adding a model from Hugging Face

For an interactive workflow, use the repository ID from the Hugging Face URL:

```sh
make install-model REPO=unsloth/Qwen3.5-9B-MTP-GGUF
```

The wizard lists the repository's GGUF quantizations, groups split model files,
identifies auxiliary files such as `mmproj` and MTP drafts, and lets you select
one main quantization plus optional auxiliary files. It runs `hf download
--dry-run` before asking for confirmation, then downloads the selected files into
`<MODELS_ROOT>/huggingface/hub/`.

After a successful download, the target regenerates the catalog and llama-swap
configuration. Start or reload llama-swap with:

```sh
make up
```

The wizard can also be run directly without regenerating the catalog/config:

```sh
./bin/hf-model.py --dry-run --root /path/to/models ORG/REPOSITORY
```

The first run uses `uv` to create a cached, isolated environment for the
script's declared `huggingface_hub` dependency. The actual file transfer is
performed by `uvx hf download`, so no system Python installation is required.

## Generating a pi-agent `models.json`

`pi` (the agent in this repository) reads `~/.pi/agent/models.json` to know
which models are available. The `bin/pi-models.py` script derives that file
from this project's `config.yaml`, so the model list stays in sync with
llama-swap. The provider is named after the local hostname (for example
`archdesktop` on this machine).

The three pi targets cover different scopes:

```sh
make pi-print         # print the JSON to stdout (no files written)
make pi-models.json   # write pi-models.json in the project root
make pi-install       # write pi-models.json and copy to ~/.pi/agent/models.json
```

All three accept `BASE=url` to override the provider `baseUrl` (for example
when sharing the file with a pi instance on another machine). Resolution
order for the base URL is:

1. `--base-url` / `make BASE=...`
2. `PI_BASE_URL` environment variable
3. `LLAMA_BASE_URL` environment variable
4. `http://127.0.0.1:8080/v1`

The reasoning flag is `false` only when the entry id contains `instruct` or
the recipe explicitly disables thinking via `--chat-template-kwargs
'{"enable_thinking":false}'`. Guardrail-removed variants (such as those with
`heretic` in the name) remain `reasoning: true`.

`pi-models.json` is gitignored; it is a build artifact, not a source file.

### Hot-reload safety

`bin/build-config.py`, `bin/catalog.py`, and `bin/pi-models.py` all compare
their new content against the existing file before writing. When the output
is byte-identical to what's on disk, the write is skipped and the mtime is
preserved. This keeps llama-swap's `-watch-config` from reloading the model
registry (and killing any in-flight request) on a no-op rebuild, so you can
safely run `make pi-print` or `make pi-install` while a model is loaded.

## Adding a new model manually

1. Drop the GGUF under `<MODELS_ROOT>/huggingface/hub/` (HF cache layout) or `<MODELS_ROOT>/lmstudio/models/<publisher>/<model>/` (LM Studio layout)
2. `make catalog` — re-scan
3. If the model matches an existing `match:` keyword in `recipes.yaml`, you're done
4. Otherwise add a recipe:

   ```yaml
   mynewmodel:
     match: "mynewmodel"
     sampling: {temp: 0.7, top_p: 0.95, top_k: 20}
   ```

5. `make config && make up`

Unknown keywords fall back to the `default` recipe (generic sampling, 128k ctx floor, parallel=1), so unconfigured models still get a working entry.

## Recipes

Each recipe describes one family of models. `match:` is a substring keyword against the model name (case-insensitive; hyphens, underscores, dots ignored). Resolution order:

| Source | Priority |
|---|---|
| `recipe.binary` | highest (per-family override) |
| CLI `--binary` | next (per-invocation override) |
| `default.binary` | next (committed global default) |
| `DEFAULT_BINARY_REL` | lowest (hardcoded fallback) |

Recipes with the **same** `match` keyword emit one llama-swap entry per recipe (siblings). For example, `qwen3.6-thinking` and `qwen3.6-instruct` both match anything with `qwen3.6` in the name, so each Qwen3.6 model produces two entries: one with thinking traces preserved, one with thinking disabled.

Recipes where one `match` is a strict substring of another are **shadowed** — the longer (more specific) keyword wins.

## Architecture notes

- The **PrismML fork** (`vendor/prism-llama.cpp`) is only required for the Bonsai 27B Q2_0 model, which uses a ternary quantization with custom kernels. Everything else uses stock `vendor/llama.cpp`.
- llama.cpp's `--fit` handles context-size selection and MoE expert offload automatically, so the generator doesn't set `-c` or `--n-cpu-moe`.
- KV cache quantization (`-ctk q8_0 -ctv q8_0`) and mmproj offload (`--no-mmproj-offload`) trigger automatically above 25 GB weight size (configurable in `bin/build-config.py`).
- Image-gen and adapter models are filtered out of the catalog (no GGUF, or no weights).
- `make` reads `MODELS_ROOT` from `.env` (gitignored). Override per-call: `make ROOT=/path`.

## Layout

```
my-agent-backend/
├── Makefile
├── README.md
├── .env.example
├── .gitignore
├── MODEL_CATALOG.{yaml,md}        generated by make catalog
├── recipes.yaml                   edit me
├── config.yaml                   generated by make config, used by bin/up
├── bin/
│   ├── catalog.py
│   ├── build-config.py
│   ├── hf-model.py
│   ├── pi-models.py
│   ├── up
│   └── bonsai-server
└── vendor/                        clone llama.cpp forks here (gitignored)
```