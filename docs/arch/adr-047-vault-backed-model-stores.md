# ADR-047: Vault-backed model stores for HuggingFace-cache-shaped services

## Title

HuggingFace-cache-shaped services read and write the model vault's HuggingFace cache directly, bind-mounted as their `HF_HUB_CACHE` — no generic vault→service symlink applier.

## Status

Proposed.

## Context

The model vault is **source-organised**: everything the Genesis sources downloaded sits under `<vault>/<source-subdir>/`, and the HuggingFace source writes its models as HuggingFace hub-cache repos (`models--org--repo/{blobs,refs,snapshots/<sha>}`) under `<vault>/huggingface/hub` (ADR-005, ADR-023).

Services want their models in a **role- or shape-organised** directory instead. ComfyUI is the canonical case: it scans a flat, role-organised tree (`<vault>/comfyui/<role>/<file>`), so ADR-026 mounts the vault root into the container and a `SymlinkApplier` materialises relative symlinks from that tree into the HF blobs.

Two shapes of service want models:

1. **Flat scanners** (ComfyUI): they need a directory of files, grouped by role. A bridge is required because the vault's shape and the scanner's shape differ.
2. **Cache scanners** (speaches, and anything else built on `huggingface_hub`): they need a HuggingFace hub cache. They call `huggingface_hub.scan_cache_dir()`, read `models--org--repo/snapshots/<sha>/README.md` for the model card, and download through `snapshot_download(cache_dir=...)`.

speaches (v0.8+) is shape 2. Since v0.8 it has **no model directory of its own**: `HF_HUB_CACHE` *is* its model store (`src/speaches/hf_utils.py`, `src/speaches/executors/*.py`). It discovers local models by scanning the cache for `models--*` repos, derives a model's metadata from its `README.md` **model card** (a repo without a card is invisible: `/v1/models` skips it, `/api/ps/{id}` fails), and downloads into the same cache. The image runs as a fixed `ubuntu` (uid 1000) and ignores `PUID`/`PGID`.

The vault's HuggingFace subtree is already exactly that layout — the HF source writes into `<vault>/huggingface/hub` with `hf_hub_download(..., cache_dir=...)`. So the vault **is** a valid speaches cache, and the integration is a bind mount, not a translation.

The alternatives:

- **(a) Share the vault's HF cache** — bind-mount `<vault>/huggingface/hub` at the container's cache path and point `HF_HUB_CACHE` at it.
- **(b) A per-service cache seeded by a symlink farm** — a `<vault>/speaches/` tree of symlinks, copied or linked into a service-owned cache. Requires fabricating `refs/`, `blobs/` and `snapshots/<sha>/` around the symlinks to make an HF cache that `scan_cache_dir()` can read, and the model card has to be carried along. Strictly more machinery than (a) for the same result.
- **(c) A Python service with a bespoke applier** — a speaches-specific plugin that mirrors ComfyUI's lifecycle. Buys nothing the YAML cannot express, and inherits the same fabrication problem as (b).

The framework's declarative DSL can express (a) exactly: a bind mount whose host side is `$options.X`, an env var, a health probe path, a web-UI path, GPU flags.

One property of (a) deserves stating up front: **the container can write to — and delete from — the vault.** `POST /v1/models/{id}` fills the vault's disk; `DELETE /v1/models/{id}` `rmtree`s the vault's cache repo (and the catalogue entry with it). The service's API is unauthenticated unless `API_KEY` is set.

## Decision

We will point HuggingFace-cache-shaped services at the model vault's HuggingFace cache by bind-mounting it into the container and setting `HF_HUB_CACHE` to the mount target.

We will **not** build a generic vault→service symlink applier. The ComfyUI symlink farm stays ComfyUI-specific (ADR-026): it exists because ComfyUI needs a flat, role-organised directory, and nothing about that premise transfers to a cache-shaped consumer.

The vault becomes **writable by service containers**: a service may download into it and delete from it. The worker does not adjust the permissions of the mounted directory — the mount target is shared vault state, and a silent `chmod`/`chown` on every start is a change to state other services and the framework's own source read. The requirement is documented instead: the cache directory must be writable by uid 1000 (the image's fixed user), and an operator whose worker runs under a different uid fixes it once with `sudo chown -R 1000:1000 <hf_cache_dir>`.

The model card is the metadata contract: a cache repo without `README.md` is not a model as far as the service is concerned, so anything that populates the vault must bring the card with the weights (ADR-048).

## Consequences

**Positive**

- One store. A model the user pulls through a service's own API lands in the vault and survives container restarts, with no second copy, no manifest and nothing to reconcile.
- The service sees what the Genesis sources acquired, and the sources see what the service downloaded — the same directory, so no import or sync step.
- No new framework code: a bind mount, an env var and a health path are all the declarative DSL needs.
- The ComfyUI symlink mechanism is not generalised into a shape it does not fit, so neither mechanism accumulates the other's exceptions.

**Negative**

- A service container can delete vault content. `DELETE /v1/models/{id}` removes the vault's cache repo. The mitigation is operational: keep the port off the public internet, or set an API key through the service's `extra_env` option.
- The container's uid must be able to write the mounted cache. The worker does **not** fix this automatically; on a node whose worker uid differs from 1000, downloads fail with a container-side `PermissionError` until the operator chowns the directory once. A silent `chmod 0777` on shared vault state was the rejected alternative.
- The shared cache grows with whatever the service downloads, and the catalogue lists those repos as models — noise on the Models page, harmless for the LLM path (the llama-swap filter is extension-based).

**Neutral**

- The ComfyUI symlink farm is unchanged: `<vault>/comfyui/<role>/<file>` → `../../huggingface/hub/...`.
- The declarative DSL still cannot choose an image or GPU flags by host hardware. A cache-shaped service that needs a CPU variant on a GPU-less node is a one-line YAML change.
- `model_aliases.json` stays the image's default. Mounting over it would need a seed-file hook kind — a follow-up, not part of this decision.

## Plan

`docs/arch/plans/plan-047-speaches-service.md` — the speaches service: the YAML spec (`services/_declarative/speaches.yaml`), the vault's HF cache bind-mounted as the container's `HF_HUB_CACHE`, and the acquire-flow extension that puts complete speech models (weights **and** model card) into the vault (ADR-048).
