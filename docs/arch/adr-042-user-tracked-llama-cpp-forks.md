# ADR-042: Declarative installables for llama-swap

## Title

Declarative installables: a user-editable install record with a `kind` discriminator, seeded with the built-in llama-swap and llama-server binaries.

## Context

The llama-swap service tracks four installables today: the `llama-swap` proxy
binary from `mostlygeek/llama-swap`, and three `llama-server` builds whose
repo coordinates and asset matchers are class attributes on
`genesis_worker/services/llama_swap/installs.py` (`LlamaSwapBinary`,
`LlamaServerCUDA`, `_UpstreamLlamaServerBinary`). `LlamaSwapService`
instantiates them in `__init__` and `installs()` returns them. The set of
tracked llama.cpp repos is fixed at code level.

That is too narrow for the motivating case, `PrismML-Eng/llama.cpp`, a fork
outside our own GitHub org whose releases publish the same
`llama-<tag>-bin-<platform>-<backend>-x64.tar.gz` asset family as upstream.

### The direction this change takes

The first cut of this work made only the *new* installables data-driven: the
four built-ins stayed as code subclasses and a `tracked_forks` list of records
was bolted on beside them. That is half-declarative. The built-ins are not
special: they are four upstream sources with an asset pattern and a binary
name, and the user's fork is a fifth source with the same shape. So the
target state is that installable construction is data-driven for all of them,
the built-ins become seed records in the same shape, and the user can add,
edit, disable, or remove any installable, built-in or not.

### Investigation: what the install and acquire layers can carry

The framework's install axis is already kind-agnostic, which is what makes
the declarative shape possible.

- **`ServiceInstall` (`genesis_worker/contracts/install.py`) is a six-method
  ABC**: `state()`, `installed_version()`, `available_versions()`,
  `binary_path()`, `install(version=...)`, `uninstall(version=...)`, plus an
  optional `source_url()`. It says nothing about where a thing comes from.
- **`AcquireSession` (`contracts/acquire.py`) is a state-machine contract**:
  `repo_id`, `state`, `view`, `submit`, `cancel`, `wait`. Each flavour
  implements it: `GithubReleaseAcquireSession` (query, download, verify,
  extract, manifest, symlink), `DockerPullAcquireSession` (streaming
  `docker pull` progress), `UvToolAcquireSession`.
- **Three flavours exist today, and all three fit the ABC**:
  `GithubReleaseTarball` + `GithubReleaseAcquireSession` (the llama-swap
  binaries), `DockerImageInstall` + `DockerPullAcquireSession`
  (`utils/services/docker_service.py`, plus the near-identical
  `ComfyUiImage` in the comfyui plugin), and `UvToolInstall` +
  `UvToolAcquireSession` (`utils/services/uv_service.py`).
- **The docker path is complete, including version listing.**
  `DockerImageInstall.available_versions()` calls `list_image_tags(repo)`,
  which asks the registry first (`DockerContainer.list_remote_tags`: the GHCR
  v2 token plus tags endpoint, or the Docker Hub v2 catalog) and falls back
  to the GitHub releases of the matching project when the registry yields
  nothing. The result is cached on disk for 15 minutes, the same TTL and the
  same cache-file shape as `GithubReleaseTarball`. `install()` returns a
  `DockerPullAcquireSession` for `<repo>:<tag>`, and `uninstall()` runs
  `docker rmi`. So "a docker-image engine tracked by registry image plus tag
  pattern" is exactly what the existing machinery does.
- **The two flavours differ in where they keep state.** A GitHub release
  install owns a directory (`<data_dir>/installs/<name>/<version>/`) with a
  `current` symlink and a `MANIFEST`; a docker image install has no host
  files, and records the pulled tag in `<state_dir>/current`. Both expose
  `binary_path()`; the docker one returns `None`, because there is no host
  binary to point a generated config at.

Conclusion: the record schema can carry a `kind` field and a factory can
dispatch on it, because the ABC and the session contract are already
flavour-neutral. No new acquisition mechanism is needed for either kind.

### Forces

- **The record schema must not be a fork in the code.** A schema hard-wired
  to GitHub releases would need a new class, a new field set, and a new
  branch every time a different kind of upstream appears. The kind has to be
  a field, and the per-kind field sets have to be separate schemas under one
  discriminator.
- **The built-ins cannot become user-owned data wholesale.** If the user's
  list replaced the seeds outright, a worker upgrade could never add a new
  built-in, and a single bad edit would take the four known-good installables
  with it. Seeds have to survive as defaults that user records override.
- **Adding an installable must not download anything.** Downloading an
  arbitrary upstream asset at add time is not acceptable: it is hundreds of
  megabytes of untrusted input fetched before the user has committed to
  anything, and it happens on every attempt. The GitHub API metadata is
  enough to validate; the archive contents can be checked after the install
  has already fetched the asset anyway.
- **The plugin boundary is a hard rule.** A plugin may import
  `genesis_worker.contracts` and `genesis_worker.utils` only (ADR-009,
  enforced by `test_plugin_boundary.py`). The plugin therefore cannot reach
  for `Settings` or for the worker's config dir on its own, and it cannot ask
  the framework to rebuild itself: `ServiceRegistry.rebuild` raises
  `KeyError` for Python plugins (ADR-036).
- **The sidecar is the established user-config mechanism.** ADR-036 put
  per-service JSON sidecars at `<config_dir>/services/<name>.overrides.json`,
  merged into `ctx.options` by `Settings.options_for`. ADR-041 extended the
  same sidecar to Python plugins (ComfyUI's `extra_args`, `extra_env`).
  llama-swap already has a sidecar key: `hardcoded_flags`.
- **`write_service_overrides` replaces, it does not merge.** The llama-swap
  status page writes `{"hardcoded_flags": ...}` wholesale, so a second key in
  the same file is one careless write away from being wiped.
- **The variant set is currently a closed literal.**
  `LlamaSwapOptions.llama_server_variant` is
  `Literal["auto", "cuda", "cpu", "vulkan"] | None`, the status page's
  dropdown is a hard-coded list, and `set_llama_server_variant` rejects
  anything else. A record set needs a variant key that did not exist when
  the literal was written.
- **The device contract only knows three backends.** `ComputeDevice.env_entry`
  answers for `cuda`, `vulkan`, and `cpu` only. A record's backend may say
  `rocm`, which the contract has no env var for.

### Open points, and what this ADR settles them as

1. **What "everything checks out" means at add time.** Settled as no
   download and an advisory release lookup: the URL must parse to an owner
   and a repo, while the GitHub API lookup is attempted to populate the
   candidate list but may fail without blocking the save. The archive
   contents are checked at install time, where the asset has been fetched
   anyway.
2. **How the asset is chosen.** Settled as: the user picks the asset from
   the release's candidate list, and the choice persists as a glob pattern
   derived from the release tag, editable by the user. The backend is
   recorded alongside the pattern, guessed from the asset name and editable.
   A fixed suffix rule alone was rejected because the backend in the name
   varies per fork and per release; tarball inspection alone was rejected
   because the backend is not in the archive contents.
3. **Whether the tracked list is manageable from the orchestrator.** Settled
   as per-node only for now. The list lives in the worker's config dir, the
   orchestrator has no write path for it (ADR-038's four endpoints do not
   cover it), and adding one is a separate API contract change.

### Numbering

This ADR keeps the `042` claim on its implementation branch. ADR-042 and
ADR-043 are also claimed by the in-flight `t-sgqw36-port-collision` and
`t-drv03p-radiance-27b` worktrees. Resolve the final sequential number at
merge time; numbering is a merge-order decision, not an implementation-branch
change.

## Decision

We will make installable construction declarative. Every installable the
llama-swap service exposes is described by a user-editable record, the
built-ins ship as seed records in that same shape, and the record's `kind`
field selects how the record is acquired.

### 1. The record schema

A discriminated union in `genesis_worker/utils/installables/records.py`, one
model per kind, with `kind` as the discriminator:

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

`primary`, `variant`, and `backend` are service-facing metadata that any
kind may carry; a docker image has no host binary, so it carries no variant
and cannot be selected as the service's engine.

Asset patterns are globs, matched with `fnmatch` case-insensitively. The
built-in patterns convert to globs without changing which asset they select
(verified against the real upstream asset names): `llama-*-bin-ubuntu-x64.tar.gz`,
`llama-*-bin-ubuntu-vulkan-x64.tar.gz`, `llama.cpp-*-cuda-*-amd64.tar.gz`,
`*_linux_amd64.tar.gz`.

### 2. Seeds and user records

The built-in seeds ship as a bundled YAML file,
`genesis_worker/services/llama_swap/data/installables.yaml`, one record per
installable, in the shape above. The user's records live in the llama-swap
sidecar under the key `installables`, as a list of plain objects, and are
merged with the seeds by name:

- A user record whose `name` matches a seed replaces that seed wholesale.
- A user record with `enabled: false` hides the installable.
- A user record with an unknown name adds a new installable.

The seeds are never modified on disk. A user who wants the built-in back
deletes their override for that name. The merge is one function,
`merge_records(seeds, overrides)`, in the same module as the record models.

The sidecar write path follows ADR-041's split: the service owns the
in-memory record list behind a setter, and the UI persists through the
worker facade. The llama-swap status page's server-flag save and revert
become read-modify-write, so saving flags no longer wipes the installable
records.

A malformed record must not take the worker down: the sidecar is a
user-editable file, and a service that refuses to construct takes the whole
registry with it. The service constructor catches a `ValidationError` that
originates from the installable records only, continues with the seeds alone,
and records the message so the Binaries page can show it. Other validation
errors still propagate.

### 3. The factory and the generic installables

`genesis_worker/utils/installables/` owns the declarative machinery:

- `records.py`: the record models and `merge_records`.
- `factory.py`: `build_installable(record, dirs, secrets)` dispatches on
  `record.kind` and returns a `ServiceInstall`.
- `github_release.py`: `GithubReleaseInstall`, the generic GitHub-release
  installable. It wraps `GithubReleaseTarball` with the record's repo
  coordinates, glob matcher, binary name, and checksums suffix.
- `docker_image.py`: `DockerImageInstall`, moved here from
  `utils/services/docker_service.py`. The move puts the installable
  implementations next to the factory that builds them; the old import paths
  keep working through re-exports.

The llama-swap plugin keeps only what is llama-swap specific: the seed file,
the plugin-side helpers for turning a GitHub URL into a candidate asset list,
and the service glue.

`LlamaSwapService.installs()` returns one installable per enabled record.
The Binaries page already renders one entry per installable from
`svc.installs()`, so a new record shows up with no new rendering work.

### 4. Add-time validation

Adding a record performs no download. The pasted URL must parse to an owner
and a repo. Accepted forms are `https://github.com/<owner>/<repo>`, the same
with a trailing `/releases`, `git@github.com:<owner>/<repo>.git`, and the bare
`<owner>/<repo>`.

The GitHub API lookup is advisory. The UI attempts it to list the latest
release's candidate assets with their sizes and to derive a suggested asset
pattern. A rate limit, private repository, missing latest release, or release
without a matching candidate produces a warning rather than blocking the
save; the user can still save a hand-written pattern. The archive contents
are checked at install time.

When the lookup succeeds, the user picks a candidate asset. The derived
pattern is shown in an editable field before the record is saved.

The GitHub token comes from `ctx.secrets.get("github_token")`, the sanctioned
accessor (`contracts/secret.py`), never from `os.environ`.

### 5. Install-time verification

The archive contents are checked where the asset has already been fetched.
`GithubReleaseAcquireSession` resolves the expected binary in the extracted
tree after extraction and fails the session when it is missing, so a fork
whose archive does not contain the binary fails loudly at install time
instead of leaving a silent "installed" state with no binary.

The resolution rule is the one the installables already use: the expected
relative path first, then a recursive search by basename. It moves out of the
plugin into `utils/install/` so the session and the installable share one
implementation.

### 6. Variant selection

Two concepts that the current code conflates are separated:

- The **variant key** identifies the installable: `cpu`, `vulkan`, `cuda`,
  and the key of each user record. It is what the dropdown shows and what
  `llama_server_variant` stores.
- The **backend** identifies the accelerator: `cpu`, `cuda`, `vulkan`,
  `rocm`. It is what selects the device environment variable, through
  `ComputeDevice.env_entry`.

`LlamaSwapOptions.llama_server_variant` widens from a closed literal to
`str | None`; the closed set moves into `set_llama_server_variant`, which
now accepts `None`, `"auto"`, and the variant key of any enabled record. The
status page's variant dropdown is built from the records.

`BuildOptions.binary_variant` carries the backend, not the key. For the
built-in records the two coincide, so generated configs are unchanged.

A record whose backend is `rocm` gets no device environment variable, because
the device contract has no `rocm` entry. That is a known gap in
`contracts/host.py`, not in this feature.

### 7. Removing an installable

Removal is in scope for every record, built-in or not. Disabling a record
hides it from `installs()` and uninstalls its installed version, deleting the
install directory and the `current` symlink for a release install, or running
`docker rmi` for an image install.

It refuses while that installable's binary is the one the config generator
would use: the user has to switch the variant first. The refusal message
names the installable.

### 8. Not in scope

- **Orchestrator management of the record list.** The list is per node. The
  extension point is a future `POST /v1/services/{name}/installables` route
  writing the sidecar through `worker.write_service_overrides`; it is an API
  contract change and belongs to its own ADR.
- **Pinning a record to a version.** A record follows the latest release.
  The Binaries page's version dropdown already installs a chosen release, and
  `InstallLayout.set_selection` already honours a `selections.yaml` pin on
  read. Neither is a new concept here.
- **Persisting the selected variant.** The variant pick is in-memory only
  today, for the built-in variants as much as for a user record, and it does
  not survive a worker restart. That gap predates this ADR.
- **A docker image as the service's engine.** A docker record has no host
  binary, so it can be installed and listed but not selected as the
  llama-server binary the config generator points at.
- **A `rocm` entry in the device contract.**
- **Reusing an add-time download at install time.** There is no add-time
  download any more. The acquire path always re-downloads on install; caching
  would speed up reinstalls and belongs to its own change.

## Status

Accepted. The open points above were settled by the approved round-2 plan and
are now implemented.

## Plan

[plan-042-declarative-installables.md](plans/plan-042-declarative-installables.md) — the
approved round-2 implementation plan for the record schema, seed records,
generic installables, service integration, UI, and tests.

## Consequences

**Positive**

- The set of installables becomes a per-node configuration instead of a
  code-level constant, for the built-ins as much as for user additions. The
  four built-ins stop being four subclasses with four copies of the same
  methods.
- A user record behaves exactly like a built-in: it appears on the Binaries
  page, it installs through the same acquisition path, it can be selected as
  the llama-server binary the config generator uses, and it survives a worker
  restart.
- Adding a record costs one or two GitHub API calls and no bandwidth. The
  candidate list shows real sizes from the API metadata.
- The record schema carries a `kind` discriminator, so a new kind of
  upstream is a new model plus one branch in the factory, not a new class
  hierarchy in the plugin.
- The docker flavour is carried by machinery that already exists
  (`DockerImageInstall`, `DockerPullAcquireSession`, registry tag listing
  with the same 15-minute disk cache as the GitHub release path).
- The install path fails loudly when an archive does not contain the expected
  binary, instead of leaving a silent "installed" state with no binary.
- The seeds are never mutated on disk, so a worker upgrade can add a
  built-in without touching the user's records, and a bad user edit cannot
  take the known-good installables with it.

**Negative**

- `DockerImageInstall.state()` now treats an image as installed when either its
  declared tag or its recorded selection tag is present. Declarative Docker
  services currently share a selection file, so accepting either tag prevents
  one service's selected tag from making another service's declared image look
  absent. Per-service selection paths remain a separate change because they
  alter the declarative-service on-disk layout.
- The record schema is wider than the four built-ins need. `primary`,
  `variant`, and `backend` are service-facing metadata on every record, and
  a reader has to know which fields apply to which kind.
- The variant option stops being a closed literal. A typo in
  `GENESIS_SERVICES__LLAMA_SWAP__LLAMA_SERVER_VARIANT` is now accepted by
  pydantic and surfaces later as "no llama-server binary for variant X". The
  service-side validation in `set_llama_server_variant` still rejects unknown
  keys.
- The asset patterns are globs now. The llama-swap proxy's old matcher
  accepted both `amd64` and `x86_64` Linux names; the seed glob accepts
  `amd64` only. The pattern is user-editable data now, so the tolerance is
  one edit away, but it is not there by default.
- The llama-swap sidecar write path changes from replace to merge. Two pages
  writing the same sidecar in the same instant can still race; the window is
  small and the loss is a re-save.
- The record list survives a restart, but the **selected variant** does not.
  Pre-existing behaviour, identical for cuda / cpu / vulkan, explicitly out
  of scope here.
- Moving `DockerImageInstall` out of `utils/services/docker_service.py` touches
  the declarative-service framework for the sake of keeping the install axis
  in one place. The old import paths keep working through re-exports.

**Neutral**

- The record list is per node, by construction: it lives in the worker's
  config dir.
- The sidecar gains an `installables` key for llama-swap the first time the
  user adds, edits, or disables a record.
- The device contract is unchanged, so a record's backend hint is limited to
  the backends the contract already knows.
- The install directory names of the four built-ins are unchanged, so
  existing installs and `selections.yaml` pins keep resolving.
