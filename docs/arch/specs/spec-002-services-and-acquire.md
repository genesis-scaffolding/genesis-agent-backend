# Spec 002: Services and acquire flows

## Goal
Implement ADR-003 (services), ADR-005 (acquire flows as a session protocol, `huggingface_hub` library). Build the `llama-swap` inference service (lifecycle + pi-models export) and the `AcquireSession` abstraction with the HuggingFace implementation. End-state: the worker can start/stop llama-swap from Python; export `pi-models.json` from `config.yaml`; drive the HF install wizard from a state machine.

This spec covers Phases 5–7 of the master plan. The running `llama-swap` is not stopped during validation; a parallel validation is run on a different port.

## Modules added

```
genesis_worker/
├── services/
│   ├── _base.py                  # InferenceService protocol + capabilities / status / result types
│   ├── _registry.py              # @register_service, all_services()
│   └── llama_swap/
│       ├── service.py            # LlamaSwapService
│       ├── lifecycle.py          # tmux + curl
│       └── export_pi_config.py       # pi-models.json
└── sources/
    ├── _base.py                  # EXTENDED: AcquireSession protocol, AcquireStep, AcquireChoice, ...
    └── huggingface.py            # EXTENDED: HfAcquireSession implementation
```

## Dependencies

```bash
uv add huggingface_hub
```

## `services/_base.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class ServiceState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ServiceCapabilities:
    can_generate_config: bool
    can_export_for_agent: bool
    can_serve_llm: bool
    can_serve_image: bool
    can_train_models: bool
    has_web_ui: bool


@dataclass(frozen=True)
class ServiceResourceEstimate:
    vram_bytes_typical: int
    vram_bytes_min: int
    cpu_cores_recommended: int


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    message: str = ""
    pid: int | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class StartResult:
    ok: bool
    message: str = ""
    pid: int | None = None


@dataclass(frozen=True)
class StopResult:
    ok: bool
    message: str = ""


@runtime_checkable
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

## `services/_registry.py`

Same pattern as `sources/_registry.py`: `@register_service` decorator + `all_services()` + auto-bootstrap via `pkgutil.iter_modules`.

## `services/llama_swap/lifecycle.py`

Lifts `bin/up` logic. Same semantics: kill existing tmux session, kill stray `llama-server`, start tmux session with `llama-swap --config <config> -listen <addr> -watch-config 2>&1 | tee -a <log>`, poll `/v1/models` for up to `health_timeout_s` seconds.

```python
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import urllib.request


def start_swap(
    config: Path, listen_addr: str, session_name: str, log_file: Path, health_timeout_s: float
) -> StartResult:
    if shutil.which("llama-swap") is None:
        return StartResult(ok=False, message="llama-swap not on PATH")
    if not config.is_file():
        return StartResult(ok=False, message=f"config not found: {config}")

    if _has_session(session_name):
        subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)
    subprocess.run(["pkill", "-9", "-f", "llama-server"], check=False)
    time.sleep(1)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = (
        f"llama-swap --config {config} -listen {listen_addr} -watch-config 2>&1 | tee -a {log_file}"
    )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, cmd],
        check=True,
    )

    if wait_ready(listen_addr, health_timeout_s):
        return StartResult(ok=True, message=f"started {session_name}", pid=None)
    return StartResult(ok=False, message=f"did not become ready in {health_timeout_s}s")


def stop_swap(session_name: str) -> StopResult:
    if not _has_session(session_name):
        return StopResult(ok=True, message="no session")
    subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)
    return StopResult(ok=True, message="killed")


def is_running(session_name: str) -> bool:
    return _has_session(session_name)


def status(session_name: str, listen_addr: str) -> ServiceStatus:
    if not _has_session(session_name):
        return ServiceStatus(state=ServiceState.STOPPED)
    try:
        with urllib.request.urlopen(f"http://{listen_addr}/v1/models", timeout=1) as r:
            return ServiceStatus(
                state=ServiceState.RUNNING if r.status == 200 else ServiceState.STARTING,
                endpoint=f"http://{listen_addr}/v1",
            )
    except Exception:
        return ServiceStatus(state=ServiceState.STARTING, endpoint=f"http://{listen_addr}/v1")


def wait_ready(listen_addr: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://{listen_addr}/v1/models", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _has_session(name: str) -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", name],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )
```

## `services/llama_swap/service.py`

```python
from __future__ import annotations

from pathlib import Path

from .._base import (
    InferenceService,
    ServiceCapabilities,
    ServiceResourceEstimate,
    ServiceStatus,
    StartResult,
    StopResult,
)
from . import lifecycle
from ._registry import register_service


@register_service
class LlamaSwapService(InferenceService):
    name = "llama-swap"
    display_name = "Llama Swap"

    def __init__(self, settings) -> None:
        self._settings = settings

    def is_available(self) -> bool:
        import shutil

        return shutil.which("llama-swap") is not None

    def is_running(self) -> bool:
        return lifecycle.is_running(self._settings.services.llama_swap.session_name)

    def runtime_endpoint(self) -> str | None:
        return f"http://{self._settings.services.llama_swap.listen_addr}/v1"

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities(
            can_generate_config=True,
            can_export_for_agent=True,
            can_serve_llm=True,
            can_serve_image=False,
            can_train_models=False,
            has_web_ui=True,
        )

    def resource_estimate(self) -> ServiceResourceEstimate:
        # Placeholder; refined later based on running models.
        return ServiceResourceEstimate(
            vram_bytes_typical=5_000_000_000,
            vram_bytes_min=2_000_000_000,
            cpu_cores_recommended=4,
        )

    def start(self) -> StartResult:
        s = self._settings.services.llama_swap
        return lifecycle.start_swap(
            config=self.config_path(),
            listen_addr=s.listen_addr,
            session_name=s.session_name,
            log_file=s.log_file or (self._settings.paths.log_dir / "llama-swap.log"),
            health_timeout_s=s.health_timeout_s,
        )

    def stop(self) -> StopResult:
        return lifecycle.stop_swap(self._settings.services.llama_swap.session_name)

    def status(self) -> ServiceStatus:
        s = self._settings.services.llama_swap
        return lifecycle.status(s.session_name, s.listen_addr)

    def wait_ready(self, timeout_s: float) -> bool:
        return lifecycle.wait_ready(
            self._settings.services.llama_swap.listen_addr,
            timeout_s,
        )

    def config_path(self) -> Path:
        # Fallback chain: explicit setting → repo-root config.yaml → XDG default
        s = self._settings.services.llama_swap
        if s.config_path is not None:
            return s.config_path
        repo_cfg = self._settings.paths.resolved_repo_root / "config.yaml"
        if repo_cfg.is_file():
            return repo_cfg
        return self._settings.paths.config_dir / "services" / "llama-swap" / "config.yaml"

    def recipes_path(self) -> Path:
        s = self._settings.services.llama_swap
        if s.recipes_path is not None:
            return s.recipes_path
        repo_recipes = self._settings.paths.resolved_repo_root / "recipes.yaml"
        if repo_recipes.is_file():
            return repo_recipes
        return self._settings.paths.config_dir / "services" / "llama-swap" / "recipes.yaml"

    # --- service-specific methods (reached via worker.service("llama-swap")) ---
    def regenerate_config(self) -> RegenerateResult: ...
    def list_recipes(self) -> list[Recipe]: ...
    def export_for_agent(self, *, base_url: str | None = None) -> dict: ...
```

(`regenerate_config` calls `services/llama_swap/generate_config.py:build_config` then `write_config`. `list_recipes` calls `recipes.load`. `export_for_agent` calls `export_pi_config.build_provider`.)

> **Superseded by [ADR-009](../adr-009-framework-plugin-boundary.md).** The service no
> longer receives `Settings` or resolves paths. It is constructed with a `ServiceContext`
> carrying resolved directories plus an opaque option slice, and owns its paths and stores
> as attributes:
>
> ```python
> def __init__(self, ctx: ServiceContext) -> None:
>     opts = LlamaSwapOptions(**ctx.options)
>     self._config_path = opts.config_path or ctx.data_dir / "config.yaml"
>     self._recipes = RecipesStore(opts.recipes_path or BUNDLED_RECIPES_PATH)
>     self._overrides = OverridesStore(self._config_path.parent / "overrides.yaml")
> ```
>
> `config_path` / `recipes_path` / `overrides_path` are properties, not fallback chains.
> `regenerate_config(catalog) -> bool` takes only the catalog. `write_models_json` and
> `pi_install_target` are named `write_agent_config` and `agent_config_target` on the
> `InferenceService` contract.

## `services/llama_swap/export_pi_config.py`

Lifts `bin/pi-models.py` logic. Replaces the hand-rolled `parse_models_section` regex with `yaml.safe_load`.

```python
from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path

import yaml

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_CONTEXT_WINDOW = 131072
DEFAULT_MAX_TOKENS = 16384
FALLBACK_PROVIDER_NAME = "llama-swap"

FIT_CTX_RE = re.compile(r"--fit-ctx(?:=|\s+)(\d+)")
MMPROJ_RE = re.compile(r"(?m)(?:^|\s)--mmproj(?:\s|\=)")
CHAT_TEMPLATE_KWARGS_RE = re.compile(r"--chat-template-kwargs\s+'([^']+)'")
INSTRUCT_TOKEN = "instruct"


def build_provider(
    config_path: Path, *, base_url: str | None = None, hostname: str | None = None
) -> dict:
    raw = yaml.safe_load(config_path.read_text())
    models_cfg = raw.get("models", {})
    provider_name = _resolve_hostname(hostname)
    base = _resolve_base_url(base_url)
    models = [_build_model(eid, body) for eid, body in models_cfg.items()]
    return {
        "providers": {
            provider_name: {
                "baseUrl": base,
                "api": "openai-completions",
                "apiKey": "local",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                    "maxTokensField": "max_tokens",
                },
                "models": models,
            }
        }
    }


def write_models_json(target: Path, provider: dict) -> bool:
    payload = json.dumps(provider, indent=2, sort_keys=True) + "\n"
    try:
        existing = target.read_text()
    except FileNotFoundError:
        target.write_text(payload)
        return True
    if existing == payload:
        return False
    target.write_text(payload)
    return True


def _build_model(entry_id: str, body: dict) -> dict:
    cmd = body.get("cmd", "")
    return {
        "id": entry_id,
        "name": body.get("name", entry_id),
        "input": ["text", "image"] if MMPROJ_RE.search(cmd) else ["text"],
        "contextWindow": _ctx(cmd),
        "maxTokens": DEFAULT_MAX_TOKENS,
        "reasoning": _reasoning(entry_id, cmd),
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }


def _ctx(cmd: str) -> int:
    matches = FIT_CTX_RE.findall(cmd)
    if matches:
        try:
            return int(matches[-1])
        except ValueError:
            pass
    return DEFAULT_CONTEXT_WINDOW


def _reasoning(entry_id: str, cmd: str) -> bool:
    m = CHAT_TEMPLATE_KWARGS_RE.search(cmd)
    if m:
        try:
            if json.loads(m.group(1)).get("enable_thinking") is False:
                return False
        except json.JSONDecodeError:
            pass
    if INSTRUCT_TOKEN in entry_id.lower():
        return False
    return True


def _resolve_hostname(explicit: str | None) -> str:
    if explicit:
        return _slug(explicit)
    try:
        return _slug(socket.gethostname())
    except OSError:
        return FALLBACK_PROVIDER_NAME


def _resolve_base_url(explicit: str | None) -> str:
    if explicit:
        return _norm(explicit)
    for env in ("PI_BASE_URL", "LLAMA_BASE_URL"):
        v = os.environ.get(env)
        if v:
            return _norm(v)
    return DEFAULT_BASE_URL


def _norm(url: str) -> str:
    return url if url.endswith("/v1") else url.rstrip("/") + "/v1"


def _slug(name: str) -> str:
    import re

    return (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")) or FALLBACK_PROVIDER_NAME
```

## `sources/_base.py` (extended)

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# ... existing ModelSource, DiscoveredModel, ModelPiece ...


@dataclass(frozen=True)
class AcquireFileGroup:
    paths: list[str]
    size: int | None
    role: str  # "main", "mmproj", "mtp", "unsupported"
    label: str
    is_sharded: bool


@dataclass(frozen=True)
class AcquireProgress:
    bytes_done: int
    bytes_total: int
    speed_bps: int
    eta_s: int


@dataclass(frozen=True)
class AcquireStep:
    kind: str  # "inspecting" | "select_files" | "confirm_storage" |
    # "downloading" | "complete" | "failed" | "cancelled"
    title: str
    prompt: str | None = None
    file_groups: list[AcquireFileGroup] | None = None
    total_bytes: int | None = None
    cache_dir: Path | None = None
    progress: AcquireProgress | None = None
    log_tail: list[str] | None = None
    can_cancel: bool = True
    error: str | None = None


@dataclass(frozen=True)
class AcquireChoice:
    """User input for one AcquireStep.

    `main_index` and `aux_indexes` are 1-based indices into the
    file_groups list (or None for steps that don't need them).
    `confirm` is True/False; None = "not applicable to this step."
    """

    main_index: int | None = None
    aux_indexes: list[int] | None = None
    confirm: bool | None = None


class AcquireState:
    """Server-side state. Held by the worker in-memory."""

    def __init__(self, source: str, repo_id: str) -> None:
        self.source = source
        self.repo_id = repo_id
        self.selected_main: AcquireFileGroup | None = None
        self.selected_aux: list[AcquireFileGroup] = []
        self.confirmed: bool = False
        self.last_step: AcquireStep | None = None


class AcquireSession(Protocol):
    source_name: str
    repo_id: str

    def current_step(self) -> AcquireStep: ...
    def submit(self, choice: AcquireChoice) -> AcquireStep: ...
    def cancel(self) -> None: ...
```

## `sources/huggingface.py` (extended)

The HF source gets a companion class implementing `AcquireSession`. Internals:

- Inspection: `HfApi().list_repo_tree(repo_id, repo_type="model", revision=..., recursive=True)` → `RemoteFile` → `AcquireFileGroup` (group sharded GGUFs).
- File grouping: lifted from `bin/hf-model.py:group_files`.
- Download: `snapshot_download(repo_id, allow_patterns=[...], cache_dir=...)` (or `hf_hub_download` per file). Cancellation via the library's token or via a `threading.Event` we check between files.
- Progress: tail the library's logger; aggregate into `AcquireProgress` + `log_tail`.

```python
class HfAcquireSession:
    def __init__(self, api: HfApi, state: AcquireState, cache_dir: Path) -> None:
        self._api = api
        self._state = state
        self._cache_dir = cache_dir
        self._cancel = threading.Event()

    def current_step(self) -> AcquireStep:
        return self._state.last_step or AcquireStep(
            kind="inspecting",
            title=f"Inspecting {self._state.repo_id}",
        )

    def submit(self, choice: AcquireChoice) -> AcquireStep:
        step = self._state.last_step
        if step is None or step.kind == "inspecting":
            # First submit after inspecting → we now have file_groups.
            # Caller submits main_index in the next round. Set prompt accordingly.
            self._state.last_step = AcquireStep(
                kind="select_files",
                title=f"Select files for {self._state.repo_id}",
                prompt="Pick one main file and any auxiliaries",
                file_groups=self._groups,
            )
        elif step.kind == "select_files":
            ...
        ...
        return self._state.last_step

    def cancel(self) -> None:
        self._cancel.set()
```

(The full implementation is detailed in plan-002; only the surface is shown here.)

## Tests

- `test_lifecycle.py`: with a fake `llama-swap` shim script on PATH, start → wait_ready (succeeds via a mock `/v1/models` response) → status==RUNNING → stop → status==STOPPED.
- `test_service_llama_swap.py`: instantiate `LlamaSwapService` with test settings; `capabilities()` returns the expected struct; `resource_estimate()` non-zero.
- `test_export_pi_config.py`: feed a fixture `config.yaml`; assert the produced JSON has the right `id`, `name`, `input`, `contextWindow`, `maxTokens`, `reasoning` for each entry. Diff against the current `pi-models.json` content (field-by-field, ignore order).
- `test_acquire_hf.py`: mock `HfApi.list_repo_tree` with a canned response (2 main GGUFs, 1 mmproj); walk the session through `inspecting → select_files → confirm_storage → downloading → complete`; assert the right `hf_hub_download` calls were made with the right `allow_patterns`.

## Verification

1. `uv run pytest genesis_worker/tests/` passes.
2. `uv run python -c "from genesis_worker.services import all_services; print([s.name for s in all_services()])"` prints `['llama-swap']`.
3. **Parallel lifecycle validation** (the running llama-swap is NOT touched):
   - Start a parallel llama-swap on a different port (e.g. `LISTEN=127.0.0.1:8081 ./bin/up` then stop it, OR call `lifecycle.start_swap(...)` with `listen_addr='127.0.0.1:8081'`).
   - Confirm `/v1/models` on `:8081` returns the same model list as `:8080`.
   - Stop the parallel instance. The running `:8080` is unaffected.
4. **Agent export diff:** `uv run python -c "from genesis_worker.services.llama_swap.export_pi_config import build_provider; from pathlib import Path; import json; print(json.dumps(build_provider(Path('config.yaml')), sort_keys=True))" | jq -S . > /tmp/new.json`; `diff <(jq -S . pi-models.json) <(jq -S . /tmp/new.json)` shows no semantic differences (field ordering / whitespace allowed to differ; `id`/`name`/`contextWindow`/`reasoning`/`input`/`maxTokens`/`cost` must match).
5. **HF acquire smoke test (no actual download):** mock the API; drive the session through one cycle; assert `snapshot_download` was called with the expected `allow_patterns` and `cache_dir`. No real network I/O.
6. `make all` still passes; `config.yaml`, `pi-models.json`, `MODEL_CATALOG.*` on disk are unchanged.
