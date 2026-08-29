# ADR-027: Redesign AcquireSession contract; extract BackgroundSession runtime

## Context

The current `AcquireSession` contract in `genesis_worker/contracts/acquire.py` carries HF-shaped types that don't generalize to other sources:

- `AcquireFileGroup` encodes GGUF sharding and HF's role taxonomy (`main` / `mmproj` / `mtp` / `safetensor`).
- `AcquireState.selected_main` and `selected_aux` are typed as `list[AcquireFileGroup]`.
- `AcquireStep.file_groups` and `AcquireChoice.main_indexes` / `aux_indexes` are HF-specific selection data.

`AcquireStep` doubles as both state-of-state (via its `kind: str` field) and UI payload (everything else). The state machine's position is buried inside an immutable snapshot whose other fields are view data; reading "what state are we in?" requires reaching through `current_step().kind`.

`HfAcquireSession` reimplements thread management, cancellation, log capture, and terminal-state translation inline (~150 lines). The same machinery was extracted for install as `BackgroundInstallSession`, but HF never benefited from that refactor. Adding a new source would require copying HF's runtime boilerplate.

The state machine has parallel state holders: `AcquireState.last_step.kind` (the kind) and `AcquireState.selected_main` / `selected_aux` (the user's picks). Mutations happen across both, and transitions reach across them.

## Decision

We will redesign the `AcquireSession` contract so state, view, and choice are typed separately, and extract a reusable runtime base class for any session that needs thread + cancel + log machinery. HF will be refactored onto the new base. Install code will not be touched in this ADR.

### 1. New contract shape in `genesis_worker/contracts/acquire.py`

- **`AcquireStateKind(StrEnum)`** replaces the free-form `kind: str`. Members: `INSPECTING`, `SELECTING`, `CONFIRMING`, `FETCHING`, `VERIFYING`, `EXTRACTING`, `COMPLETE`, `FAILED`, `CANCELLED`. Sessions visit only the kinds they need.
- **`AcquireState`** is a mutable dataclass holding workflow position + progress. Fields: `kind: AcquireStateKind`, `repo_id: str`, `confirmed: bool = False`, `bytes_done: int = 0`, `bytes_total: int = 0`, `log_tail: list[str]`, `failure: str | None = None`. No HF-shaped fields. Sessions that need typed selection extend this with their own subclass.
- **`AcquireView`** (renamed from `AcquireStep`) is a frozen dataclass holding the UI payload. Fields: `kind`, `title`, `prompt`, `progress`, `log_tail`, `can_cancel`, `error`, `cache_dir`, `total_bytes`. No `file_groups` — sessions extend with their own view subclass for selection data.
- **`AcquireChoice`** carries `confirm: bool | None = None` only. Sessions that need typed selection fields extend it (e.g. HF adds `main_indexes` / `aux_indexes`).
- **`AcquireSession(ABC)`** exposes: `source_name: str` (class attr), `state` (property), `view()`, `submit(choice)`, `cancel()`, `wait()`.
- **`AcquireFileGroup`** is removed from the contract. It moves to `genesis_worker/sources/huggingface/acquire.py`.

### 2. New base class in `genesis_worker/utils/background_session.py`

- **`BackgroundSession(AcquireSession)`** owns the runtime:
  - Daemon worker thread + supervisor that catches `_Canceled` (→ `CANCELLED`) and any other `Exception` (→ `FAILED`, populating `state.failure`).
  - `threading.Event` for cancellation, exposed as `self._cancel_event`. Subclasses check it between long-running steps.
  - Log tail buffer (`self._log_tail`) with thread-safe `_append_log(line)`.
  - `wait()` blocks on the thread (no-op if the thread has not started).
  - `_start()` spawns the thread. Subclasses decide when: eager in `__init__` for pipelines, lazy after user confirmation for wizards.
- Subclasses provide `view()`, `submit()`, and `_run_inner()`. The base does NOT manage a Python logging handler — sessions that need to mirror a library's logger do so themselves.

### 3. HF-specific types move into `genesis_worker/sources/huggingface/acquire.py`

- **`AcquireFileGroup`** — unchanged shape, now HF-local.
- **`HfAcquireState(AcquireState)`** — adds `selected_main: list[AcquireFileGroup]`, `selected_aux: list[AcquireFileGroup]`.
- **`HfAcquireView(AcquireView)`** — adds `targets: list[AcquireFileGroup]`.
- **`HfAcquireChoice(AcquireChoice)`** — adds `main_indexes: list[int] | None`, `aux_indexes: list[int] | None`.
- **`HfAcquireSession(BackgroundSession)`** — uses the base runtime. HF writes only inspection, file classification/grouping, selection validation, per-file download with stderr capture. Inspect synchronously in `__init__` so first `view()` returns `SELECTING`. Start the worker thread on `_handle_confirming(confirm=True)` via `_start()`.

### 4. Out of scope (deliberate)

- **`genesis_worker/contracts/install.py`, `genesis_worker/utils/install/`, `genesis_worker/services/*/install*.py` are not touched.** `InstallSession`, `BackgroundInstallSession`, and the three install backends (`cptr`, `comfyui`, `llama_swap`) keep their current shape. The new `BackgroundSession` is offered as the path forward; a future ADR migrates install when there's appetite.
- **`AcquireStep` → `AcquireView` rename is in-scope.** The name better reflects "view of state" rather than "a step in a workflow." Mechanical rename; pyright catches misses. Install code imports `AcquireStep`; the rename touches those imports (one-line changes).
- **`AcquireFileGroup` move is in-scope.** No other source uses it.
- **`AcquireState.last_step` → `AcquireState.kind` is in-scope.** The new state object has a typed kind field; the old "last_step" field goes away.

## Status

Proposed.

## Consequences

**Positive:**

- One runtime base class for any thread-driven session; HF drops ~150 lines of inline machinery.
- Contract carries no HF-shaped types; new sources can implement selection with their own types without polluting shared code.
- `state.kind` is a typed enum — callers no longer compare strings.
- View / state split makes the state machine's mental model honest: state is state, view is view, choice is choice.
- Adding a new source = write a session + a UI page; no thread/cancel/log boilerplate to copy.
- The contract vocabulary is now uniform: *state* (workflow position), *view* (UI snapshot), *choice* (user input).

**Negative:**

- `AcquireStep` rename touches every file that imports it. Mechanical but pervasive (facade, UI, tests, install code).
- HF's `view()` must return `HfAcquireView` (a subclass); the UI does `isinstance(view, HfAcquireView)` to access `targets`. Same coupling as today, slightly more typed.
- The base's `_run_inner` contract is implicit: subclass checks cancel + raises on failure. A missed raise means the thread completes "successfully" even on partial failure. Same risk as today's `_download_worker`.
- Two runtime base classes coexist after this ADR: `BackgroundInstallSession` (install) and `BackgroundSession` (acquire). A future ADR unifies them; until then, the responsibility split is fuzzy.

**Neutral:**

- `AcquireState` is mutable; transitions mutate fields in place. Same GIL-protected pattern as today's `last_step` reassignment.
- `_Canceled` continues to live in `utils/install/session.py`. `BackgroundSession` imports it from there. Promoting it to the contract can wait until a second consumer needs it.
- The state's `repo_id: str` is session-defined (HF uses `org/name`; install would use whatever). The contract treats it as an opaque identifier.

## Concrete changes

1. **`genesis_worker/contracts/acquire.py`** — rewrite per Decision §1. Delete `AcquireFileGroup`. Add `AcquireStateKind`. Rename `AcquireStep` to `AcquireView`. New `AcquireState` and `AcquireChoice` shapes.

2. **`genesis_worker/utils/background_session.py`** — new file with `BackgroundSession`. Imports `_Canceled` from `genesis_worker.utils.install.session`.

3. **`genesis_worker/sources/huggingface/acquire.py`** — add `AcquireFileGroup` (moved from contract), `HfAcquireState`, `HfAcquireView`, `HfAcquireChoice`. Rewrite `HfAcquireSession(BackgroundSession)`. Drop the inline `_LogTailHandler` class; use the base's `_append_log` for synthetic lines and a small HF-local context manager for stderr capture during `hf_hub_download`.

4. **`genesis_worker/facade.py`** — `acquire_step()` returns `view()`, `submit_acquire(choice)` delegates to `submit(choice)`, `cancel_acquire` delegates to `cancel()`. `list_acquire_sessions()` keeps the terminal-cleanup side effect.

5. **`genesis_worker/sources/huggingface/ui/acquire.py`** — imports change (`HfAcquireView`, `HfAcquireChoice`); widget logic mostly stays the same.

6. **Tests** — `genesis_worker/tests/test_acquire_hf.py`, `genesis_worker/tests/test_sources_huggingface.py` update for the new types. No new coverage beyond existing behavior.

7. **NOT touched** — `genesis_worker/contracts/install.py`, `genesis_worker/utils/install/`, `genesis_worker/services/cptr/install.py`, `genesis_worker/services/comfyui/install.py`, `genesis_worker/services/llama_swap/installs.py`.

## Verification

- `uv run pytest -q` passes.
- `uv run pyright` clean (or only pre-existing errors on `cli/hf_model.py:32` and `tests/test_docker_container.py:616`).
- `uv run ruff check genesis_worker` clean (or only pre-existing errors).
- UI smoke test: start a fresh acquire from the HF page, walk through `SELECTING` → `CONFIRMING` → `FETCHING` → `COMPLETE`, cancel mid-download.
