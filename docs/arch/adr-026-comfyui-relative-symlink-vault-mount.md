# ADR-026: ComfyUI model vault binding via relative symlinks

## Title

ComfyUI consumes models through bind-mounted vault root + relative symlinks so a single source of truth survives across the host/container boundary.

## Status

Accepted. Supersedes the bind-mount shape described in ADR-025 (*Symlink safety considerations* and *Lifecycle*); the symlink applier itself remains. Depends on ADR-023 (`vault_path` on `PluginContext`) and ADR-025.

## Context

ADR-025 chose shape (3): the ComfyUI service bind-mounts `<vault>/comfyui/` as the container's `/opt/comfyui/app/models`, and a `SymlinkApplier` writes symlinks under that bind mount pointing at blobs in the rest of the vault. Two things were left implicit in that ADR and bit us during integration:

### 1. Absolute symlinks do not survive the host/container boundary

Symlinks written by the applier originally used absolute host paths, e.g.

```
/home/gentran1991/.local/share/genesis-worker/vault/comfyui/diffusion_models/foo.safetensors
  → /home/gentran1991/.local/share/genesis-worker/vault/huggingface/hub/models--.../blobs/abc
```

On the host this resolves correctly because the absolute path exists. Inside the container it does not — the container has no view of `/home/gentran1991/.local/...`. ComfyUI sees a dangling symlink and silently fails to load the model.

`Path.relative_to()` was the first fix attempted (commit history). It does not work either: the blob lives in a **sibling** subdir of the vault (`vault/huggingface/...`) versus the symlink's location (`vault/comfyui/<role>/`), so `relative_to(parent)` raises `ValueError` (only descendants are accepted). The correct call is `os.path.relpath(target, start)` which handles arbitrary relationships.

### 2. ComfyUI's models dir is fixed at `/opt/comfyui/app/models` and only one is supported

The image's `entrypoint.sh` and `folder_paths.py` use `os.path.dirname(os.path.realpath(__file__)) + "/models"` as the default `models_dir`. Earlier binding (`<vault>/comfyui` → `/opt/comfyui/app/models`) was correct in spirit but required ComfyUI to look at `/opt/comfyui/app/models/<role>/`, which is exactly where the symlinks live. That part worked.

But it coupled the bind mount to the vault's `comfyui/` subdir. If the user ever wanted their models under a different vault subtree, the bind mount and the symlink layout would have to be re-architected together.

### 3. ComfyUI exposes `--models-directory <path>`

`folder_paths.py` (verified inside the running container) honours two CLI args:

```python
if args.base_directory:
    base_path = os.path.abspath(args.base_directory)
else:
    base_path = os.path.dirname(os.path.realpath(__file__))

if args.models_directory:
    models_dir = os.path.abspath(args.models_directory)
else:
    models_dir = os.path.join(base_path, "models")
```

`models_dir` is then used to auto-register all the standard roles (`folder_names_and_paths["checkpoints"] = ([os.path.join(models_dir, "checkpoints")], ...)` etc.). Setting `--models-directory /vault/comfyui` is the canonical ComfyUI way to relocate the entire models dir without rewriting `folder_paths.py` or mounting `/opt/comfyui/app/models` to anything external.

### 4. The vault root is the only container-side mount point that resolves symlinks correctly

Once a symlink target uses a relative path like `../../huggingface/hub/.../blob`, it resolves **relative to the symlink's parent directory**, which is the same on both host and container **if and only if** the parent directory has the same relative structure in both. With the original mount shape:

```
host:    vault/comfyui/diffusion_models/<symlink>
container: /opt/comfyui/app/models/diffusion_models/<symlink>  (since vault/comfyui mounted there)
```

Going `../..` from `/opt/comfyui/app/models/diffusion_models/` lands at `/opt/comfyui/app/`, **not** at `/home/gentran1991/.local/.../vault/`. The relative path can only resolve correctly if the parent directory is part of a tree whose mount root contains the symlink target.

The fix: mount the vault root inside the container as `/vault`, so `/vault/comfyui/<role>/<symlink>` is the symlink's location and `/vault/huggingface/.../blob` is the target — both share the same parent root (`/vault`), so `../../huggingface/...` resolves correctly.

## Decision

### Bind mount shape

```python
volumes = {
    "/opt/comfyui/python":           <data_dir>/comfyui/data/python,
    "/opt/comfyui/app/custom_nodes": <data_dir>/comfyui/data/custom_nodes,
    "/opt/comfyui/app/input":        <data_dir>/comfyui/data/input,
    "/opt/comfyui/app/output":       <data_dir>/comfyui/data/output,
    "/opt/comfyui/app/user":         <data_dir>/comfyui/data/user,
    "/vault":                        <vault_path>,        # vault root, NOT <vault>/comfyui
}

extra_args = [
    "--models-directory", "/vault/comfyui",   # tells ComfyUI where its models dir is
    *user_extra_args,
]
```

`<vault_path>` is `<vault>` on the host (ADR-023). `<vault>/comfyui/` becomes `/vault/comfyui/` inside the container. ComfyUI's `folder_paths.py` registers `/vault/comfyui/<role>/` as the search path for every standard role.

We do **not** mount `<vault>/comfyui/` to `/opt/comfyui/app/models` — that path is empty inside the container and never consulted, because `--models-directory` redirects ComfyUI's lookup away from it.

### Symlink shape

Every symlink the applier writes targets a **relative path** from the symlink's parent directory:

```
/vault/comfyui/diffusion_models/foo.safetensors
  → ../../huggingface/hub/models--Comfy-Org--MiniMax-Music-3/blobs/<sha>
```

`os.path.relpath(blob_path, symlink_path.parent)` produces this. From `/vault/comfyui/diffusion_models/` two `..` levels land at `/vault/`, then the rest of the path is exactly the location of the blob inside the vault.

The same symlink on the host resolves identically: `vault/comfyui/diffusion_models/foo.safetensors` resolves `vault/comfyui/diffusion_models/../../huggingface/...` = `vault/huggingface/...`. **The relative path is host/container agnostic by construction**, because both mount shapes put the symlink's parent inside the vault subtree.

### Symlink applier rules

1. Compute the target with `os.path.relpath(target_path, symlink_path.parent)`. If that fails (different drives on Windows; degenerate edge cases), fall back to absolute and log — the symlink will work on the host only.
2. When the applier sees an existing symlink whose resolved target matches the catalog blob, **it is still a no-op only if the raw link target is relative**. An absolute symlink that happens to resolve correctly on the host is wrong for the container and must be rewritten. This catches the legacy-symlink migration scenario: old symlinks (created before this ADR) coexist with new ones in the same vault and converge on relative form on the next `apply()`.
3. Catalog identity stored in yaml is `(source, entry, piece_filename)` — not absolute blob path. A HuggingFace snapshot rotation that keeps the filename intact does not invalidate the symlink.

### Migration path for legacy symlinks

When `apply()` is invoked after this ADR lands, the no-op check uses `os.readlink()` (raw link text, not the resolved path) to verify the symlink uses a relative target. Legacy absolute symlinks fail the check and are rewritten in-place:

- On disk: `unlink()` then `symlink_to(relative_target)`. The rewrite touches the bind mount but not the underlying blob.
- The yaml row does not change — only the on-disk symlink does.
- The "Resync to disk" button in the Models UI invokes this path; one click migrates all legacy symlinks.

`prune_dangling()` does **not** migrate. It only removes symlinks whose targets are missing. The migration belongs to `apply()` because that is where the relative-target invariant lives.

## Consequences

**Positive**

- One mount (`/vault`) covers both the symlink-source tree (`/vault/comfyui/<role>/`) and the symlink-target tree (`/vault/huggingface/...`). The bind-mount shape is regular: every model-related path inside the container is rooted at `/vault/`.
- Symlinks are written once and read everywhere. They resolve identically on host and container because the relative path is computed against a parent that has the same relative structure in both views.
- Legacy absolute symlinks migrate automatically on the next `apply()` — no manual `find … -type l -exec …` needed.
- `--models-directory` is a ComfyUI-native knob; we are not patching `folder_paths.py` or shipping a `extra_model_paths.yaml` from the framework.

**Negative**

- The vault root is now exposed at `/vault/` inside the container. The ComfyUI process can read every model in the vault, not just the ones symlinked into `<vault>/comfyui/<role>/`. That is fine for our use case (single-user, host-trusted container) but is the wrong shape for a multi-tenant deployment where the container should only see a curated subset. A future hardened mode could mount only `<vault>/comfyui/` plus an explicit allow-list of source dirs — out of scope here.
- The "no-op only if relative" rule is subtle and tripped us once already. Covered by `test_apply_rewrites_existing_absolute_symlink_as_relative`.
- The applier cannot tell from a yaml row alone whether the corresponding on-disk symlink is broken — it has to compare `os.readlink()` (raw text) against the relative form. The check is cheap but the contract is non-obvious; the test is the spec.

**Neutral**

- `<vault>/comfyui/` is the only vault subtree consumed by ComfyUI. Other services that want to mount the vault should follow the same pattern (mount vault root, expose via service-specific search path) rather than bind-mounting per-source subdirs.
- ADR-025's *Symlink safety considerations* section is partially superseded: the four gotchas it listed (file ownership, stale symlinks, cross-snapshot rotation, cross-filesystem traversal) all still apply, but the symlink-target format and the absolute/relative distinction are now specified here. The two ADRs complement each other.
- `extra_model_paths.yaml` is no longer written by the service. (Earlier drafts of this work did write it; we walked that back because ComfyUI's `--models-directory` is sufficient.)

## Plan

The implementation is in `feature/comfyui-vault-symlinks` (commits `939517d`…`93600a6`). It has been merged to `main` as of `9789ac8`.