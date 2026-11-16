# ADR-048: Speech models in the HuggingFace acquire flow

## Title

The HuggingFace acquire flow surfaces a speech model as one layout bundle (weights together with their companions) and always fetches the model card.

## Status

Proposed.

## Context

The acquire flow's contract has been "one selectable model = one weight file (plus its GGUF shards)": `sources/huggingface/acquire.py` keeps only `.gguf`, `.safetensors` and `.rad` files from the repo tree, classifies each by extension, and lets the user pick the primary group plus optional auxiliaries (`mmproj`, `mtp`).

That shape fits GGUF and safetensors. It does not fit a speech model:

- A Kokoro TTS model is `model.onnx` **plus** `voices.bin` — the model is not loadable without the voices file.
- A faster-whisper / CTranslate2 STT model is `model.bin` **plus** `config.json`, `tokenizer.json` and `vocabulary.txt` (and `preprocessor_config.json`).
- A Piper model is `model.onnx` plus `config.json`.
- speaches additionally requires the **`README.md` model card** before it will list a repo at all: a cache repo without a card is invisible to `/v1/models` and fails `/api/ps/{id}` (`src/speaches/hf_utils.py`, `src/speaches/routers/utils.py`).

So the vault could not hold a model that speaches can serve: the flow never surfaced the speech files, never grouped them, and never fetched the card. The alternatives:

- **(a) Keep the flow LLM-shaped** and let speaches download its own models. Then the vault holds the model (ADR-047) but only through the service's own API, not through the Genesis UI — the "acquire a model" path a user already knows stays blind to speech.
- **(b) Extend the flow with per-file roles** and let the user assemble a speech model from individually selectable files. The user would have to know that `voices.bin` belongs with `model.onnx` — the flow would be offering a way to get it wrong, and a model acquired without its card would be invisible to the service.
- **(c) Extend the flow with layout bundles** — a speech model is surfaced and downloaded as one group whose file set is derived from a small table of known layouts, with the model card as part of the bundle.

The layout table is a copy of speaches' own `download_model_files` allow-patterns (`src/speaches/executors/{kokoro,piper,whisper,parakeet}.py`), so it has to track upstream — a new upstream layout is not acquirable until the table gains a row. The fallback is always the service's own `POST /v1/models/{id}`, which still lands in the vault (ADR-047).

The table's rows are:

| layout | required | optional |
|---|---|---|
| `kokoro` | `model.onnx`, `voices.bin` | — |
| `piper` | `model.onnx`, `config.json` | — |
| `ctranslate2` | `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.*` | `preprocessor_config.json` |
| `parakeet` | `encoder-model*.onnx`, `decoder_joint-model*.onnx`, `vocab.txt` | `config.json` |

The parakeet row mirrors upstream's current ONNX export (`onnx_asr`'s `NemoConformerTdt`, whose files speaches looks up as `encoder-model.onnx`, `decoder_joint-model.onnx`, `vocab.txt` and `config.json`). The older `*.nemo` NeMo export is not a row: speaches' own parakeet filter rejects those repos, so no layout could serve them.

## Decision

We will extend the HuggingFace acquire flow with **layout-aware bundles**: a speech model is surfaced and downloaded as one group whose file set is derived from a small table of known layouts (kokoro, piper, ctranslate2, parakeet). The files of a bundle must live in the same directory in the repo, because that is where they must land together in the cache.

The model card (`README.md`) is **part of a speech bundle** — a bundle without it is not offered, and its download failure fails the acquire loudly, because a card-less model is useless to the service. For every **other** acquire (GGUF / safetensors / radiance) the card is fetched **best-effort** after the weights: a failure appends a warning to the acquire log and does not fail the acquire, because the weights are already on disk.

We will not let the user assemble a speech model from individually selectable files.

A bundle's label carries the directory it was found in (`speaches: kokoro (2 files) - voices`). The label is the key the acquire UI resolves a selection back to, and two directories can hold the same layout — without the directory, both bundles would carry the same label and the UI would resolve the selection to the first of them.

The role vocabulary grows by one value, `speech`. The acquire contract itself does not change: `contracts/acquire.py` is role-agnostic, and the role is a plain string on the source's own `AcquireFileGroup`. `contracts/classify.py` is deliberately untouched — the LLM filter is extension-based, and the shared classifier also feeds ComfyUI's role dropdown.

## Consequences

**Positive**

- TTS/STT models become acquirable through the Genesis UI, and they land in the vault **complete** — weights *and* card — which is what makes ADR-047's "the vault is the store" true for speech as well.
- The user cannot assemble a broken model: the bundle is the unit of selection and of download.
- The model card becomes available to any future card-based consumer, not just speaches.
- The GGUF / safetensors / radiance selection rules are untouched, but the progress accounting was reworked to **per-file bytes**: the old code added a group's whole size once per file (a three-shard GGUF triple-counted the bytes) and printed the byte count as the file index. The model card's bytes stay out of `bytes_total`. The existing tests still hold after that rework.

**Negative**

- The acquire contract gains a second shape — a bundle — next to the single-file group. The source's own group dataclass carries the role string, but the code that consumes groups (the selection UI, the validation) now has to know about the `speech` role.
- The layout table must track upstream's allow-patterns. A repo whose layout is unknown is not acquirable through the UI.
- One extra request per acquire for the card (a few KB); for non-speech acquires it is best-effort, for a speech bundle it is mandatory.
- A bundle in a sub-directory is not visible to the service: its card lands at `snapshots/<sha>/<dir>/README.md`, while speaches reads the card from the snapshot root. The same-directory constraint is intentional — a bundle's files must land together in the cache (plan risk 8).
- The catalogue gains cosmetic noise: `voices.bin` / `model.bin` classify as role *main*, and a Piper/Parakeet entry carries the note "no model weights on disk" because every one of its pieces classifies as *config*. Neither affects the LLM config — the llama-swap filter is extension-based, so TTS/STT entries never reach it.

**Neutral**

- `contracts/classify.py` is unchanged. Extending `WEIGHT_EXTS` with `.onnx` / `.nemo` would tidy the notes but would also change ComfyUI's role dropdown — a wider blast radius than this decision needs. Follow-up if the noise matters.
- The acquire UI is a Streamlit page, so the role filter that makes a bundle selectable is not unit-tested; the logic behind it (the group roles) is.

## Plan

`docs/arch/plans/plan-047-speaches-service.md` — the layout table, the bundle builder, the selection rules, and the mandatory/best-effort model-card fetch in `sources/huggingface/acquire.py`.
