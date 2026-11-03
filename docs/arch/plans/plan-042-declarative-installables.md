# Plan (round 2): declarative installables for llama-swap (task `t-9xp8ik`)

Worktree: `/home/gentran/Documents/worktrees/genesis-agent-backend/t-9xp8ik-track-llamacpp-forks`
Branch: `se/t-9xp8ik-track-llamacpp-forks` (uncommitted, as required by the stage)

> **Review amendment:** Sections 4.6 and 9.1 are amended after review: the GitHub API lookup is advisory; see ADR-042 section 4.

---

## 1. What changed since round 1, and where I agree or differ

Round 1 was rejected on its premise, not its details. This round answers all four points of the rework brief.

| Rejection point | This round |
|---|---|
| Drop the add-time download validation | Dropped. No asset is downloaded when a record is added. The archive-content check moves to install time, where the asset has been fetched anyway. The UI shows candidate sizes from the GitHub API metadata only. |
| Fully declarative, not half-declarative | The four built-ins stop being code subclasses. They become seed records in the same shape as a user's fork, in a bundled YAML file. The user can add, edit, disable, and remove any record, built-in or not. |
| `kind` is a field, extensible to other kinds | The record is a pydantic discriminated union on `kind`. Two kinds are implemented: `github_release` and `docker_image` (registry image plus tag pattern), built on the machinery that already exists. |
| Fresh investigation of the install/acquire contracts | Done, in section 3. The finding is that `ServiceInstall` and `AcquireSession` are already flavour-neutral, and that the docker flavour (registry tag listing plus `docker pull`) exists in full. |

**One reconciliation with the task text, stated explicitly.** The task description says "Validation happens when the fork is added, not at install time" and lists "the extracted tree contains a `llama-server` binary" as part of "everything checks out". The rejection overrides the second half of that: no download at add time. So the split is: every assertion that can be made from API metadata (repo resolves, repo has a release, the release carries an asset the acquisition path can take) is made when the record is added, and the one assertion that needs the bytes (the archive contains the expected binary) is made when the install has already fetched the asset. That is the only reading of the two instructions that satisfies both. It is a change from round 1, not a silent drop.

**Where I differ from the brief, slightly.** The brief says "design the record schema so `kind` is a field, not a fork in the code". A schema with a `kind` discriminator but only one implemented kind is a schema that lies about its own shape, so this plan implements the `docker_image` kind as well rather than leaving it as a promise. The cost is one thin adapter over `DockerImageInstall` (which already exists) and a move of that class next to the factory. If the reviewer prefers a smaller diff, the `docker_image` model and the factory branch are the only pieces to drop; the schema, the seeds, the merge, and the UI all stay as they are.

---

## 2. Goal

The user opens the llama-swap service UI in the worker and adds a GitHub URL for a llama-cpp fork. The fork is validated from GitHub metadata, persisted as a record, and appears on the Binaries page as an installable beside the built-ins. It installs through the existing GitHub release path, can be selected as the llama-server binary the config generator uses, and survives a worker restart. The same machinery lets the user edit or remove the built-ins, and lets a record of a different kind (a docker image) be added without touching the schema.

---

## 3. Fresh investigation: what the install and acquire layers can carry

### 3.1 The contracts are flavour-neutral

`ServiceInstall` (`genesis_worker/contracts/install.py`) is a six-method ABC: `state()`, `installed_version()`, `available_versions()`, `binary_path()`, `install(version=...)`, `uninstall(version=...)`, plus an optional `source_url()`. It says nothing about where a thing comes from.

`AcquireSession` (`contracts/acquire.py`) is a state-machine contract: `repo_id`, `state`, `view`, `submit`, `cancel`, `wait`. Each flavour implements it.

Three flavours exist today and all three fit the ABC:

| Flavour | ServiceInstall | Session | Version listing |
|---|---|---|---|
| GitHub release tarball | `LlamaSwapBinary`, `LlamaServerCUDA`, `_UpstreamLlamaServerBinary` (plugin) | `GithubReleaseAcquireSession` | `GithubReleaseTarball.available_versions()`, releases with a matching asset, 15-min disk cache |
| Docker image | `DockerImageInstall` (`utils/services/docker_service.py`), `ComfyUiImage` (plugin) | `DockerPullAcquireSession` | `list_image_tags(repo)`: registry first, GitHub-releases fallback, 15-min disk cache |
| uv tool | `UvToolInstall` (`utils/services/uv_service.py`) | `UvToolAcquireSession` | PyPI JSON, single entry |

So a record with a `kind` field and a factory that dispatches on it is the natural shape. No new acquisition mechanism is needed for either kind.

### 3.2 The docker path is complete, including version listing

- `DockerContainer.list_remote_tags(repo)` dispatches on the registry prefix: GHCR repos use the v2 token plus tags endpoint, everything else uses the Docker Hub v2 catalog. Both are public for public images. Any failure returns `[]`, which the caller reads as "registry unreachable", not as an error.
- `list_image_tags(repo)` falls back to the GitHub releases of the matching project when the registry yields nothing (`utils/registry_tags.py`), which is what the recent `fix/ghcr-tag-listing` commit added.
- `DockerImageInstall.install(version=tag)` returns `DockerPullAcquireSession(image=f"{repo}:{tag}")`; `uninstall()` runs `docker rmi`; `installed_version()` reads `<state_dir>/current`; `state()` checks `docker image inspect`.
- `binary_path()` returns `None`: there is no host binary for a container service.

Conclusion: "a docker-image engine tracked by registry image plus tag pattern" is exactly what the existing machinery does. The record kind maps onto it without new machinery.

### 3.3 The GitHub release path, and what it does not check today

`GithubReleaseTarball` takes `repo_owner`, `repo_name`, an `asset_for(assets)` callback, `binary_rel`, and an optional `checksums_url`. `GithubReleaseAcquireSession` queries the release, downloads the asset, verifies the sha256 when a checksums asset is configured, extracts, writes the manifest, and sets the `current` symlink.

It never checks that the extracted tree contains the expected binary. The plugin's `binary_path()` resolves it afterwards with `_resolve_binary` (legacy relative path first, then a recursive search by basename), and returns `None` when it is missing. So today a wrong asset produces an install session that reports "complete" and an installable that reports "not installed". The install path does not fail loudly. This round makes it fail: after extraction, the session resolves the expected binary and fails the session when it is absent.

Two facts from round 1's investigation still hold and drive the asset design: the backend (cpu / vulkan / cuda / rocm) is encoded in the asset **name**, not in the archive contents, and `llama-server` sits at the end of the archive stream (member 87 of 88 in the Prism fork tarball), so a cheap partial-download probe does not exist. Both are why the asset is picked by name, by the user, from API metadata.

### 3.4 The plugin boundary and the rebuild path constrain the design

A plugin may import `genesis_worker.contracts` and `genesis_worker.utils` only (ADR-009, enforced by `test_plugin_boundary.py`). `ServiceRegistry.rebuild` raises `KeyError` for Python plugins (ADR-036), so the llama-swap service cannot ask the framework to re-read its configuration. The record list therefore follows ADR-041's split: the service holds the records in memory behind a setter, the UI persists them through `worker.write_service_overrides`, and `Settings.options_for` puts them back into `ctx.options` on the next worker start.

`write_service_overrides` replaces the whole file. The llama-swap status page writes `{"hardcoded_flags": ...}` wholesale, so the sidecar write path for this service has to become read-modify-write before a second key can live there. That is a latent bug today (the file only ever held one key) and a live one after this change.

### 3.5 Baseline gate state

`uv run pytest -q` green (1268 passed, 31 deselected), `ruff check` green, `pyright` green. `ruff format --check genesis_worker` **already fails** on three pre-existing files: `genesis_worker/tests/conftest.py`, `genesis_worker/tests/test_facade.py`, `genesis_worker/tests/test_lifecycle.py`. They come from the merge of `fix/ghcr-tag-listing`, not from this task.

---

## 4. The design

### 4.1 The record schema

`genesis_worker/utils/installables/records.py`:

```python
class InstallableBase(BaseModel):
    name: str                        # unique; also the install directory name
    label: str | None = None         # display label; defaults to name
    enabled: bool = True
    primary: bool = False            # the service's primary installable
    variant: str | None = None       # key under which the service selects it
    backend: str | None = None       # accelerator hint for the device env var

class GithubReleaseRecord(InstallableBase):
    kind: Literal["github_release"] = "github_release"
    repo: str                        # "owner/name"
    asset_pattern: str               # glob over release asset names
    binary_name: str = "llama-server"
    checksums_asset_suffix: str | None = None
    source_url: str | None = None    # defaults to https://github.com/<repo>

class DockerImageRecord(InstallableBase):
    kind: Literal["docker_image"] = "docker_image"
    image: str                       # "ghcr.io/org/name"
    tag_pattern: str | None = None   # glob over registry tags; None = all
    default_tag: str = "latest"

InstallableRecord = Annotated[
    Union[GithubReleaseRecord, DockerImageRecord], Field(discriminator="kind")
]
```

Asset patterns are globs, matched case-insensitively with `fnmatch`. The four built-in matchers convert to globs without changing which asset they select; verified against the real upstream asset names:

| Built-in | Old matcher | Seed glob |
|---|---|---|
| llama-swap proxy | `^.*_linux_(?:amd64\|x86_64)\.tar\.gz$` | `*_linux_amd64.tar.gz` |
| upstream CPU | `^llama-.+-bin-ubuntu-x64\.tar\.gz$` | `llama-*-bin-ubuntu-x64.tar.gz` |
| upstream Vulkan | `^llama-.+-bin-ubuntu-vulkan-x64\.tar\.gz$` | `llama-*-bin-ubuntu-vulkan-x64.tar.gz` |
| genesis CUDA | `^llama\.cpp-(?:b\d+\|v\d+\.\d+\.\d+)-cuda-\d+\.\d+-amd64\.tar\.gz$` | `llama.cpp-*-cuda-*-amd64.tar.gz` |

The one behaviour change: the proxy's old matcher also accepted `x86_64` Linux names; the glob accepts `amd64` only. The pattern is user-editable data now, so the tolerance is one edit away, but it is not there by default. Called out in the ADR's Consequences.

### 4.2 Seeds and user records

The built-ins ship as a bundled YAML file, `genesis_worker/services/llama_swap/data/installables.yaml`:

```yaml
installables:
  - kind: github_release
    name: llama-swap
    label: llama-swap proxy
    primary: true
    repo: mostlygeek/llama-swap
    asset_pattern: "*_linux_amd64.tar.gz"
    binary_name: llama-swap
    checksums_asset_suffix: "_checksums.txt"
  - kind: github_release
    name: llama-server-cuda
    label: llama-server (CUDA, genesis-scaffolding)
    variant: cuda
    backend: cuda
    repo: genesis-scaffolding/llama.cpp-cuda
    asset_pattern: "llama.cpp-*-cuda-*-amd64.tar.gz"
    binary_name: llama-server
  - kind: github_release
    name: llama-server-cpu
    label: llama-server (CPU, upstream)
    variant: cpu
    backend: cpu
    repo: ggml-org/llama.cpp
    asset_pattern: "llama-*-bin-ubuntu-x64.tar.gz"
    binary_name: llama-server
  - kind: github_release
    name: llama-server-vulkan
    label: llama-server (Vulkan, upstream)
    variant: vulkan
    backend: vulkan
    repo: ggml-org/llama.cpp
    asset_pattern: "llama-*-bin-ubuntu-vulkan-x64.tar.gz"
    binary_name: llama-server
```

The names are unchanged from today, so existing install directories (`<data_dir>/installs/llama-server-cpu/`) and `selections.yaml` pins keep resolving. The order matches today's `installs()` order, so the Binaries page renders in the same order.

The user's records live in the llama-swap sidecar under the key `installables` (the sidecar shape from the task description; the round-1 key name `tracked_forks` is gone, because the shape is no longer fork-specific):

```json
{
  "installables": [
    {"kind": "github_release", "name": "llama-server-prism", "variant": "prism",
     "backend": "vulkan", "repo": "PrismML-Eng/llama.cpp",
     "asset_pattern": "llama-*-bin-ubuntu-vulkan-x64.tar.gz", "binary_name": "llama-server"}
  ]
}
```

The merge, in `records.py`:

```python
def merge_records(seeds: list[InstallableRecord], overrides: list[InstallableRecord]) -> list[InstallableRecord]:
    """Seeds first in seed order, then user additions.

    An override replaces the seed with the same name wholesale, including
    ``enabled: false``, which hides the record. The seeds are never modified.
    """
```

The service filters `enabled` when it builds `installs()`, and hands the full list to the UI so a disabled record can be re-enabled.

### 4.3 The generic machinery

New package `genesis_worker/utils/installables/`:

- `records.py`: the models and `merge_records`.
- `factory.py`:
  ```python
  @dataclass(frozen=True)
  class InstallDirs:
      data_dir: Path
      cache_dir: Path
      state_dir: Path

  def build_installable(record: InstallableRecord, dirs: InstallDirs, *, secrets=None) -> ServiceInstall
  ```
  dispatches on `record.kind`.
- `github_release.py`: `GithubReleaseInstall` (the generic installable, generalised from `_UpstreamLlamaServerBinary`), plus `ReleaseAsset`, `ReleaseCandidate`, `match_asset`, `derive_asset_pattern`, and `find_asset_by_suffix` (moved from the plugin).
- `docker_image.py`: `DockerImageInstall`, moved here from `utils/services/docker_service.py`.

The move keeps the installable implementations next to the factory that builds them. `utils/services/docker_service.py` and `utils/services/__init__.py` re-export `DockerImageInstall`, so `from genesis_worker.utils.services import DockerImageInstall` and `from genesis_worker.utils.services.docker_service import DockerImageInstall` keep working and no test import changes.

`utils/install/` stays the layout-and-manifest package and gains one module:

- `utils/install/binary.py`: `resolve_binary(install_root, binary_name, *, legacy_rel=None)`, the current `_resolve_binary` moved verbatim. It is shared by the installable's `binary_path()` and by the acquire session's post-extract check, so there is one implementation of the resolution rule.

`utils/acquire/github_release.py` gains one public helper and one check:

```python
def fetch_latest_release(repo_owner, repo_name, *, api_base=None, auth_token=None) -> dict[str, Any]
    """The latest release record (tag_name + assets), or raises on a 404."""
```

and, in `GithubReleaseAcquireSession._run_inner`, after extraction:

```python
found = resolve_binary(install_root, backend.binary_rel)
if found is None:
    shutil.rmtree(install_root, ignore_errors=True)
    raise RuntimeError(f"archive contains no {backend.binary_rel!r} binary")
```

### 4.4 The llama-swap plugin

`genesis_worker/services/llama_swap/installs.py` (the four classes) is deleted. In its place:

- `genesis_worker/services/llama_swap/data/installables.yaml`: the seeds above.
- `genesis_worker/services/llama_swap/installables.py`: the plugin-side pieces.
  - `BUNDLED_INSTALLABLES_PATH` and `load_seed_records()` (parse the YAML into validated records; a malformed seed file raises at construction, loudly).
  - `parse_repo_url(raw) -> (owner, repo)`: accepts `https://github.com/<owner>/<repo>`, the same with a trailing `/releases` or `/releases/latest`, `git@github.com:<owner>/<repo>.git`, and the bare `<owner>/<repo>`. Anything else raises `ValueError` naming the accepted forms.
  - `fork_slug(owner, repo) -> str`: lowercase, every non-alphanumeric run collapsed to `-`.
  - `guess_backend(asset_name) -> str`: `cuda` / `vulkan` / `rocm` (also `hip`) / `cpu`.
  - `candidate_assets(assets, tag) -> list[ReleaseAsset]`: keeps assets ending in `.tar.gz` / `.tgz` / `.tar.xz` / `.zip`, mentioning `x64` or `amd64`, and mentioning none of `win`, `macos`, `android`, `arm64`, `aarch64`, `s390x`, `riscv`, `cudart`, `ui.tar`, `xcframework`. On `PrismML-Eng/llama.cpp` that is exactly the 8 Linux x64 server builds; the Windows, macOS, Android and `cudart` bundles are dropped.

### 4.5 The service

`genesis_worker/services/llama_swap/options.py`:

- `LlamaSwapOptions.installables: list[InstallableRecord] = []` (the user's records, straight from the sidecar through `ctx.options`).
- `LlamaSwapOptions.llama_server_variant: str | None = "auto"` (the closed literal widens; the closed set moves into the service).

`genesis_worker/services/llama_swap/service.py`:

- `__init__`: load the seeds, take the user records from `ctx.options`, merge, and build one installable per enabled record into `self._installables: dict[str, ServiceInstall]`.
- `installs()`: the enabled records' installables, in record order.
- `primary_installable()`: the record with `primary: true`.
- `is_available()`: the primary installable's `binary_path()` is not `None`.
- `variant_keys()`: the `variant` of every enabled record that has one.
- `set_llama_server_variant(variant)`: accepts `None`, `"auto"`, or a known variant key; anything else raises `ValueError` as today.
- `installable_records()` / `user_installable_records()` / `set_installable_records(user_records)`: the UI's read and write surface.
- `disable_installable(name)`: hides the record and uninstalls it; refuses while that installable's binary is the one `effective_llama_server_binary()` resolves to, with a message naming it.
- `list_release_candidates(repo_url) -> ReleaseCandidate`: the metadata-only GitHub query (URL parse, repo resolve, latest release, candidate assets with sizes).
- The variant/backend split: the **variant key** identifies the installable (what the dropdown shows, what `llama_server_variant` stores); the **backend** identifies the accelerator (what `BuildOptions.binary_variant` carries, what `ComputeDevice.env_entry` matches on). `_variant_for_binary_path` becomes a backend lookup over the records. For the built-ins the two coincide, so generated configs are unchanged.
- Constructor hardening: a `ValidationError` originating from the installable records is caught narrowly, the seeds are used alone, and the message is recorded for the Binaries page to display. Other validation errors still propagate.

### 4.6 The UI

`genesis_worker/services/llama_swap/ui/binaries.py`, a new panel above the installable list:

1. **Installables panel.** A caption explaining that the four built-ins ship as seeds and that any record can be added, edited, or disabled.
2. **The form.** A `st.selectbox` for `kind` (`github_release` / `docker_image`), then kind-specific fields:
   - `github_release`: a GitHub URL field and a **Fetch release assets** button. The button calls `svc.list_release_candidates(url)`, which is one API call, no download. On success the panel shows the latest tag and a `st.selectbox` of the candidate assets labelled with name and size in MB (from the API metadata). The selected asset seeds an editable **Asset pattern (glob)** field, pre-filled with `derive_asset_pattern(asset.name, tag)` (the asset name with every occurrence of the release tag replaced by `*`). A **Backend** selectbox is pre-filled with `guess_backend(asset.name)`. If the API call fails (rate limit, private repo), the panel says so and leaves the pattern field to be typed by hand: adding a record must not require the lookup.
   - `docker_image`: an **Image** field (`ghcr.io/org/name`) and an optional **Tag pattern (glob)** field.
   - Common: **Name** (pre-filled from the slug, e.g. `llama-server-prismml-eng-llama-cpp`), **Variant key** (pre-filled with the slug), **Backend** (for release records), **Label** (optional).
3. **Add / Save.** The button validates the record (name unique, variant key unique, the URL parses) and attempts the advisory release lookup, then persists it: the sidecar is read, the `installables` key is rewritten with the new user-record list, the sidecar is written, and the service's in-memory list is updated. If the in-memory update is rejected, the sidecar is rolled back. A failed write leaves the in-memory state unchanged.
4. **The record list.** One row per record: label, kind, source, pattern or image, variant key, backend, installed state. Buttons: **Edit** (loads the record into the form as a draft) and **Disable**.
5. **Edit.** The name is shown read-only once a record exists: renaming it would orphan its install directory and its `selections.yaml` pin. Everything else (label, pattern, backend, variant key, image, tag pattern) is editable. Saving an edited seed writes a user record with the same name, which overrides the seed.
6. **Disable.** Calls `svc.disable_installable(name)`, which uninstalls the installed version and hides the record. The refusal when the record is the active engine binary is shown inline.

`genesis_worker/services/llama_swap/ui/status.py`:

- The variant dropdown becomes `["(legacy)", "auto"] + svc.variant_keys()`. A persisted variant that is no longer known falls back to `auto` with a caption, instead of raising on `option_labels.index(current)`.
- The server-flag save and revert become read-modify-write on the sidecar, so saving flags no longer wipes the installable records. The revert pops `hardcoded_flags` instead of writing `{}`.

### 4.7 Persistence, restart, and the orchestrator

- The record list is per node: it lives in `<config_dir>/services/llama_swap.overrides.json`.
- It survives a worker restart: `Settings.options_for` merges the sidecar into `ctx.options`, the service reads the records at construction, and the seeds are re-applied underneath.
- The orchestrator does not manage it in this change. ADR-038's four endpoints do not cover it; the extension point is a future `POST /v1/services/{name}/installables` writing the sidecar through `worker.write_service_overrides`, which is an API contract change and belongs to its own ADR.

---

## 5. Phases

Each phase is independently testable. Phases 1 to 4 need no network.

**Phase 1: the generic machinery.**
`utils/installables/{__init__,records,factory,github_release,docker_image}.py`, `utils/install/binary.py`, the `DockerImageInstall` move with its re-exports, `fetch_latest_release` in `utils/acquire/github_release.py`, and the post-extract binary check in `GithubReleaseAcquireSession`.

**Phase 2: the plugin's seeds and helpers.**
`services/llama_swap/data/installables.yaml`, `services/llama_swap/installables.py`, delete `services/llama_swap/installs.py`.

**Phase 3: the service.**
`options.py` and `service.py`: the record list, the installable construction, the variant/backend split, the CRUD methods, the candidate query.

**Phase 4: the UI.**
`ui/binaries.py` (the panel) and `ui/status.py` (the dynamic dropdown, the sidecar merge).

**Phase 5: docs and the gate.**
README section, the ADR (already rewritten), the test suite, the four-command gate.

---

## 6. Files touched

**New**

| File | Change |
|---|---|
| `genesis_worker/utils/installables/__init__.py` | exports the record models, the factory, the installable classes |
| `genesis_worker/utils/installables/records.py` | the record union, `merge_records` |
| `genesis_worker/utils/installables/factory.py` | `InstallDirs`, `build_installable` |
| `genesis_worker/utils/installables/github_release.py` | `GithubReleaseInstall`, `ReleaseAsset`, `ReleaseCandidate`, `match_asset`, `derive_asset_pattern`, `find_asset_by_suffix` |
| `genesis_worker/utils/installables/docker_image.py` | `DockerImageInstall`, moved from `utils/services/docker_service.py` |
| `genesis_worker/utils/install/binary.py` | `resolve_binary`, moved from the plugin's `installs.py` |
| `genesis_worker/services/llama_swap/data/installables.yaml` | the four seed records |
| `genesis_worker/services/llama_swap/installables.py` | the seed loader and the plugin-side URL, slug, backend, and candidate helpers |
| `genesis_worker/tests/test_installables.py` | the record models, the merge, the factory, the generic installables |
| `genesis_worker/tests/test_llama_swap_installables.py` | the plugin's seeds, helpers, service wiring, and UI-facing behaviour |

**Modified**

| File | Change |
|---|---|
| `genesis_worker/utils/install/__init__.py` | export `resolve_binary` |
| `genesis_worker/utils/acquire/github_release.py` | `fetch_latest_release`; the post-extract binary check in the session |
| `genesis_worker/utils/services/docker_service.py` | import `DockerImageInstall` from its new home; re-export it |
| `genesis_worker/utils/services/__init__.py` | re-export `DockerImageInstall` from its new home |
| `genesis_worker/utils/services/compose_service.py` | import path for `DockerImageInstall` |
| `genesis_worker/services/llama_swap/options.py` | `installables` field; widen `llama_server_variant` |
| `genesis_worker/services/llama_swap/service.py` | build installables from records; the variant/backend split; the CRUD methods; the candidate query; the lenient record parse |
| `genesis_worker/services/llama_swap/ui/binaries.py` | the installables panel |
| `genesis_worker/services/llama_swap/ui/status.py` | dynamic variant dropdown; sidecar writes become merge |
| `genesis_worker/tests/test_service_llama_swap.py` | the installable classes are gone; the assertions move to the generic class and the records |
| `genesis_worker/tests/test_install_tarball.py` | the matcher tests become seed-pattern tests; `find_asset_by_suffix` moves with the code |
| `README.md` | a short section on the installable records |
| `docs/arch/adr-042-user-tracked-llama-cpp-forks.md` | rewritten for this design, kept at 042, renumber-on-merge noted |

**Deleted**

| File | Change |
|---|---|
| `genesis_worker/services/llama_swap/installs.py` | the four classes become seed records; `LlamaSwapBinary`, `LlamaServerCPU`, `LlamaServerVulkan`, `LlamaServerCUDA` are replaced by `GithubReleaseInstall` built from a record |

Not touched: `contracts/` (no contract change), `settings.py`, `facade.py`, `registries.py`, `api/`, the declarative `configure` panel (it only renders for `DeclarativeServiceBase`, which llama-swap is not).

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

Plus the integration suite, because the new tests bind a local socket (the default `addopts` is `-m "not integration"`):

```
uv run pytest -m integration -q
```

Note on `ruff format`: the baseline already fails `ruff format --check` on `genesis_worker/tests/{conftest.py,test_facade.py,test_lifecycle.py}`. `uv run ruff format genesis_worker` (which `AGENTS.md` tells the agent to run) fixes them. Include that reformat and say so in the PR description, or leave the gate red and report it. Do not ship a red gate silently.

New tests, by name (`test_<unit>_<scenario>_<expected>`):

`genesis_worker/tests/test_installables.py`

- `test_record_union_parses_github_release_kind`
- `test_record_union_parses_docker_image_kind`
- `test_record_union_rejects_unknown_kind`
- `test_merge_records_keeps_seed_order`
- `test_merge_records_override_replaces_seed`
- `test_merge_records_disabled_override_hides_seed`
- `test_merge_records_appends_unknown_name`
- `test_build_installable_returns_github_release_install_for_github_kind`
- `test_build_installable_returns_docker_image_install_for_docker_kind`
- `test_build_installable_rejects_unknown_kind`
- `test_match_asset_matches_glob_case_insensitively`
- `test_derive_asset_pattern_replaces_release_tag`
- `test_derive_asset_pattern_falls_back_when_tag_absent`
- `test_github_release_install_resolves_nested_binary`
- `test_github_release_install_available_versions_filters_by_pattern` (integration, fake GitHub server)
- `test_github_release_install_uses_checksums_asset_when_configured` (integration, fake GitHub server)
- `test_docker_image_install_lists_registry_tags` (integration, fake registry or monkeypatched `list_image_tags`)
- `test_acquire_session_fails_when_archive_has_no_binary` (integration, fake GitHub server)
- `test_acquire_session_succeeds_when_archive_has_binary` (integration, fake GitHub server)

`genesis_worker/tests/test_llama_swap_installables.py`

- `test_seed_records_define_the_four_builtin_installables`
- `test_seed_record_patterns_match_real_upstream_asset_names`
- `test_installs_returns_one_installable_per_enabled_record`
- `test_installs_preserves_seed_order`
- `test_disabled_record_is_not_in_installs`
- `test_user_record_overrides_seed_with_same_name`
- `test_primary_installable_is_the_llama_swap_proxy`
- `test_variant_keys_include_user_record_variant`
- `test_set_llama_server_variant_accepts_record_variant`
- `test_set_llama_server_variant_rejects_unknown_variant`
- `test_backend_of_selected_record_drives_build_options`
- `test_disable_installable_uninstalls_and_hides_record`
- `test_disable_installable_refuses_while_its_binary_is_active`
- `test_malformed_record_does_not_break_service_construction`
- `test_parse_repo_url_accepts_github_url`
- `test_parse_repo_url_accepts_releases_suffix`
- `test_parse_repo_url_accepts_ssh_form`
- `test_parse_repo_url_rejects_non_github_host`
- `test_fork_slug_is_lowercase_and_dashed`
- `test_guess_backend_maps_asset_name_to_backend`
- `test_candidate_assets_exclude_windows_macos_and_cudart`
- `test_list_release_candidates_returns_asset_sizes_from_api_metadata` (integration, fake GitHub server)
- `test_list_release_candidates_rejects_unknown_repo` (integration, fake GitHub server)

Updated in `genesis_worker/tests/test_service_llama_swap.py`: the `isinstance` assertions that named the removed classes become assertions on `GithubReleaseInstall` and on the record's fields. The "four installables, in this order" assertions stay valid, because the seed order matches.

Use the existing fake-GitHub-server fixture pattern from `genesis_worker/tests/test_install_tarball.py` (`_FakeServer`, `GENESIS_INSTALL_GITHUB_API`) and `service_ctx(tmp_path)` from `genesis_worker/tests/_factories.py`.

Manual check, after the gate is green (the DoD is a UI behaviour, so it needs a human or a live worker):

1. Binaries page, **Installables** panel: the four built-ins are listed, marked as seeds.
2. Add a record: kind `github_release`, paste `https://github.com/PrismML-Eng/llama.cpp`, **Fetch release assets**. The panel shows the latest tag and the 8 candidate assets with their sizes, without downloading anything.
3. Pick `llama-prism-...-bin-ubuntu-vulkan-x64.tar.gz`; the pattern field shows `llama-*-bin-ubuntu-vulkan-x64.tar.gz`; the backend is `vulkan`. Save.
4. The new record appears in the list and as an entry on the Binaries page with a version dropdown. Install it; the binary resolves at `<root>/llama-server`.
5. Status page: the variant dropdown now lists the new record's variant key. Select it, **Regenerate config**, and check the generated cmd points at the new binary and the env block carries `GGML_VK_VISIBLE_DEVICES` on the AMD host.
6. Edit the record's pattern, save, and check the change took effect without a worker restart.
7. Restart the worker: the record is still there and still selectable.
8. Add a `docker_image` record for a public image, install it, and check the pull progress renders on the Binaries page.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| The change is bigger than the original task: a new framework package, a moved class, a deleted plugin module | It is what "fully declarative" costs. The phases are ordered so the generic machinery lands first and the plugin rewrite second. The reviewer can drop the `docker_image` kind (one model plus one factory branch) without touching anything else. |
| The asset pattern becomes a glob, so the proxy's `x86_64` tolerance is gone | The pattern is user-editable data now. Called out in the ADR's Consequences rather than hidden. |
| A user record with a bad shape takes the worker down | The constructor catches a records-scoped `ValidationError`, falls back to the seeds alone, and surfaces the message on the Binaries page. Other validation errors still propagate. |
| Widening `llama_server_variant` from a `Literal` to `str` loses pydantic validation for env-var configuration | The service-side `set_llama_server_variant` still rejects unknown keys. An unknown key from the env surfaces at config-regen time as "no llama-server binary for variant X", which is loud. |
| `write_service_overrides` replaces the whole file, and the status page uses it | The status page's flag save and revert become read-modify-write. Two concurrent writers can still race; the window is one click and the loss is a re-save. |
| GitHub API rate limits (60/hr unauthenticated) | One call per lookup, plus one per tracked repo per 15 minutes on the Binaries page (the release cache is on disk). The add form works without the lookup, so a rate limit does not block adding a record. Set `github_token` for headroom; the token is already plumbed through `ctx.secrets`. |
| A fork versions its assets without the release tag in the name | The derived pattern degrades to `*<asset name>`, which matches one release only. The pattern is editable, and the Binaries page then shows "No matching asset found upstream" rather than failing silently. |
| The record list survives a restart, but the **selected variant** does not | Pre-existing behaviour, identical for cuda / cpu / vulkan. Explicitly out of scope in the ADR; a follow-up would persist `llama_server_variant` in the same sidecar. |
| A record's backend is `rocm`, which the device contract does not know | `ComputeDevice.env_entry` returns `None`, so no device env var is injected. The binary still runs and sees all GPUs. Adding a `rocm` entry to `contracts/host.py` is out of scope. |
| The post-extract binary check changes install behaviour for the existing four | Today an archive without the expected binary reports "installed" with no binary. The check turns that into a failed session with a clear message. The four built-ins all pass it: the proxy tarball has `llama-swap` at the root, and the llama-server tarballs carry exactly one `llama-server` (verified by listing the upstream and Prism tarballs). |
| Moving `DockerImageInstall` touches the declarative-service framework | The old import paths keep working through re-exports; the move is mechanical. |
| The install path always re-downloads | Unchanged from today. Caching would speed up reinstalls and belongs to its own change. |

---

## 9. The open points, settled with a recommendation (flag at the approval gate)

1. **What "everything checks out" means at add time.** Amended after review: the URL must parse, while the GitHub API lookup is advisory and may fail without blocking the save; there is no add-time download. The archive-content check moves to install time, where the session now fails loudly when the expected binary is missing. See ADR-042 section 4.
2. **How the asset is chosen.** Recommended: the user picks from the candidate list, and the pick persists as a tag-derived, user-editable glob, with the backend recorded alongside it. Alternatives: a fixed suffix rule (cannot express the per-fork backend) or tarball inspection (the backend is not in the archive contents, and it needs a download).
3. **Orchestrator scope.** Recommended: per node only. The extension point is a future `POST /v1/services/{name}/installables` route, an API contract change that belongs to its own ADR.
4. **New in this round: does the `docker_image` kind ship now.** Recommended: yes, as a thin adapter over the machinery that already exists, so the `kind` field is not a promise. It can be dropped without touching the schema, the seeds, the merge, or the UI.

---

## 10. What I did not finish

- **No implementation, no commit.** Per the stage, the only file I wrote is the rewritten ADR (uncommitted, on `se/t-9xp8ik-track-llamacpp-forks`). `git status` shows exactly one untracked file, `docs/arch/adr-042-user-tracked-llama-cpp-forks.md`.
- **No plan file in `docs/arch/plans/`.** `AGENTS.md` writes that file after the user approves the plan; this plan lives in the pipeline's plan journal and in `/tmp/se-t-9xp8ik-plan-r2.md`, not in the repo.
- **The ADR is Proposed, not Accepted.** It carries a recommended default for each open point rather than a settled user decision. If the approval gate picks a different option, the ADR is edited before the code lands.
- **The generic machinery is designed, not built.** The record models, the factory, the seed file, and the UI flow are specified down to the field level, but no code exists yet and nothing has been run against a live GitHub API for the candidate listing.
- **The ADR keeps its 042 number** even though `t-sgqw36-port-collision` claims 042 and `t-drv03p-radiance-27b` claims 043. The file records the clash and the renumber-on-merge obligation.
