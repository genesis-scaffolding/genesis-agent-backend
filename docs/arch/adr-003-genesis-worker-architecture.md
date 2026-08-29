# ADR-003: Genesis Worker architecture — facade with pluggable sources and services

## Title
Genesis Worker architecture — facade with pluggable sources and services

## Context
The current `my-agent-backend` repo is a single-machine toolkit for managing GGUF models behind `llama-swap`. It is a working collection of `bin/` scripts (catalog walker, config builder, pi-models export, HF download wizard, llama-swap lifecycle) plus a hand-curated `recipes.yaml` and three generated artifacts (`MODEL_CATALOG.{yaml,md}`, `config.yaml`, `pi-models.json`). There is no importable API; the only consumers are `make` targets and shell users.

We are turning this repo into the **Genesis Worker** half of the Genesis Infrastructure Toolkit (the orchestrator lives in the sibling repo `genesis-infrastructure-toolkit/`). The worker must:

- Expose a **Streamlit UI** reachable from a phone on Tailscale.
- Provide a public Python API so the CLI, the UI, and (in the future) the orchestrator can all drive it the same way.
- Be **extensible in two axes**:
  1. **Model sources** — HuggingFace cache today, LM Studio today, ModelScope / Civitai / others tomorrow. Each source has its own on-disk layout, its own naming convention, and (sometimes) its own remote-acquisition flow.
  2. **Inference services** — `llama-swap` today, ComfyUI / AIToolkit / vLLM tomorrow. Each service has its own binary, port, config-file format, lifecycle, and (sometimes) its own concept of "recipe" or "preset."

Past projects have shipped UI/CLI/automation on top of a script-shaped codebase and paid the price when the API surface grew informally. We are deliberately introducing a facade **before** the UI is built, so the Streamlit app calls one object and that object owns the implementation.

The orchestrator's ADRs (`genesis-infrastructure-toolkit/docs/arch/`) already establish conventions: a single facade object (`Toolkit`), nested settings, pydantic-settings, Streamlit UI, Typer CLI. The worker follows the same conventions where applicable.

## Decision

### Single facade

`GenesisWorker` is the only object the CLI, the UI, and any future external consumer touches. Internal modules are reached only through the facade. This is enforced by code review in v1; tooling enforcement is v2.

### Two extension axes as protocols + registries

Both axes are pluggable via a Protocol + in-tree registry pattern:

```python
# sources/_base.py
class ModelSource(Protocol):
    name: str
    display_name: str
    can_acquire: bool

    def is_available(self) -> bool: ...
    def local_path(self) -> Path: ...
    def walk(self) -> Iterable[DiscoveredModel]: ...
    def acquire(self, request: AcquireRequest) -> AcquireSession: ...


# services/_base.py
class InferenceService(Protocol):
    name: str
    display_name: str

    def is_available(self) -> bool: ...
    def is_running(self) -> bool: ...
    def runtime_endpoint(self) -> str | None: ...
    def capabilities(self) -> ServiceCapabilities: ...
    def resource_estimate(self) -> ServiceResourceEstimate: ...
    def start(self) -> StartResult: ...
    def stop(self) -> StopResult: ...
    def status(self) -> ServiceStatus: ...
    def wait_ready(self, timeout_s: float) -> bool: ...
```

### Auto-discovery via in-tree registry

Each module under `genesis_worker/sources/` or `genesis_worker/services/` registers itself via a decorator. The registry auto-imports submodules at facade-init time. Adding a new source or service is a single file; no central enum to update, no consumer code to change.

```python
# sources/_registry.py
@register_source
class HuggingFaceSource:
    name = "huggingface"
    ...
```

### Per-source `AcquireSession` protocol

Acquisition flows (HF's multi-step wizard, future ModelScope's flow, etc.) are state machines, not REPL scripts. Each source implements an `AcquireSession` that emits UI-agnostic `AcquireStep` snapshots. The Streamlit page renders whatever step comes back — no HF-specific UI code in the page.

```python
class AcquireStep(Protocol):
    kind: Literal[
        "inspecting",
        "select_files",
        "confirm_storage",
        "downloading",
        "complete",
        "failed",
        "cancelled",
    ]
    title: str
    prompt: str | None
    file_groups: list[AcquireFileGroup] | None
    total_bytes: int | None
    progress: AcquireProgress | None
    log_tail: list[str] | None
    can_cancel: bool
    error: str | None
```

### Multi-service peers, not a singleton

`llama-swap`, `ComfyUI`, `AIToolkit`, `vLLM` are peers. Each has its own port, process, config, and lifecycle. The facade exposes `start_service(name)`, `stop_service(name)`, `service_status(name)`. The dashboard renders one tile per service. No single "active service" — multiple services may run concurrently.

### Capability-driven dashboard

Each service reports `ServiceCapabilities` (what it can do) and `ServiceResourceEstimate` (what it costs). The Streamlit dashboard reads these and renders buttons/panels accordingly. No hardcoded `if service == "llama-swap"` branches in the UI.

```python
@dataclass(frozen=True)
class ServiceCapabilities:
    can_generate_config: bool
    can_export_for_agent: bool
    can_serve_llm: bool
    can_serve_image: bool
    can_train_models: bool
    has_web_ui: bool
```

### Per-source and per-service settings

Settings nest under `Settings.sources.<name>` and `Settings.services.<name>`. Adding a new source/service = adding a new pydantic model and registering it. No central enum.

## Status
Accepted

## Consequences

Positive:
- One facade; one mental model for "how do I use the worker."
- New sources and services drop in without touching framework code, UI code, or other sources/services.
- The dashboard adapts to whatever services are registered — no UI rewrites when ComfyUI ships.
- Acquire flows don't leak HF specifics into the UI; future sources with different flows work for free.
- Service-specific methods (`LlamaSwapService.regenerate_config()`) are reachable via `worker.service("llama-swap")` while the common lifecycle (`start/stop/status`) stays uniform.
- Convention matches the orchestrator's `Toolkit` facade (ADR-001 in the orchestrator repo).

Negative:
- In-tree registry means sources/services are not pip-installable as separate packages. We don't need that yet; if/when we do, an entry-points layer can be added without changing consumers.
- `can_acquire` on `ModelSource` is a soft capability — sources that don't acquire (LM Studio) just raise or return a "not supported" session. Could be tighter; v2 may split into `ReadableSource` and `AcquirableSource` Protocols.
- Per-source acquire flow abstraction adds indirection. For HF today, the indirection is minimal (one class wraps the wizard steps). For a future source with a single-click download, the abstraction may feel heavy. Acceptable; the abstraction is cheap when not used.

Neutral:
- `AcquireSession` lifecycle (server-side state, cancellation, progress polling) is in-memory in v1. SQLite-backed durability is v2.


