# ADR-005: HF acquisition via `huggingface_hub` library; acquire flows as a session protocol

## Title
HF acquisition via `huggingface_hub` library; acquire flows as a session protocol

## Context
`bin/hf-model.py` is the current HF install wizard. It uses `huggingface_hub.HfApi.list_repo_tree` for inspection (declared as an inline-script dependency via PEP 723) and shells out to `uvx hf download --dry-run` and `uvx hf download` for the actual file transfer.

This has two problems:

1. **PEP 723 inline scripts are a packaging anomaly.** The other `bin/` scripts use plain `python3` and the project has no `pyproject.toml`. The wizard is the only one that brings its own dependency model. With the worker becoming a real project, this inconsistency ends.
2. **Subprocess shelling to `uvx hf download` is a control-flow loss.** Cancellation requires killing the subprocess; progress is whatever the CLI happens to print; we cannot integrate the download with the rest of the worker's lifecycle.

The orchestrator repo establishes the principle that "we orchestrate existing tools, we don't fork them" (orchestrator `ARCHITECTURE.md`). The HuggingFace `hf` CLI is the upstream tool. We can either shell it (current) or call its underlying library (`huggingface_hub`) directly. Calling the library is strictly more capable (cancellation, progress callbacks, structured errors) while still using the upstream implementation.

A second concern: HF's wizard is multi-step (paste repo → list files → pick main → pick auxiliaries → see size → confirm → download with progress). Future sources (ModelScope, Civitai, etc.) will have different flows. The UI must not hardcode HF's flow.

## Decision

### Use `huggingface_hub` library directly

`huggingface_hub` becomes a project dependency (`uv add huggingface_hub`). The HF source uses:

- `HfApi.list_repo_tree(repo_id, repo_type="model", revision=..., recursive=True)` for inspection (same as today).
- `snapshot_download(repo_id, ...)`, `hf_hub_download(...)`, or the new `hf_hub_download` async variants for the transfer.

The `uvx hf download` subprocess is gone. Progress is surfaced via `huggingface_hub`'s built-in logging or via a callback wrapper. Cancellation uses the library's native APIs (`snapshot_download` accepts a `token` cancel flag, or we wrap the call in a thread we can kill cleanly).

### Acquire flows are a session protocol

A source's acquisition flow is a state machine, not a script. Each source implements:

```python
class AcquireSession(Protocol):
    source_name: str
    repo_id: str
    state: AcquireState

    def current_step(self) -> AcquireStep: ...
    def submit(self, choice: AcquireChoice) -> AcquireStep: ...
    def cancel(self) -> None: ...
```

The Streamlit page renders whatever step the session is in:

```python
step = worker.acquire_step(session_id)
if step.kind == "select_files":
    render_file_group_form(step.file_groups)  # user picks main + aux
    worker.submit_acquire(session_id, choice)
elif step.kind == "confirm_storage":
    render_size_warning(step.total_bytes)
    if st.button("Confirm"):
        worker.submit_acquire(session_id, choice)
elif step.kind == "downloading":
    render_progress(step.progress, step.log_tail)
```

For v1, only the HF source implements `AcquireSession`. Future sources implement their own session type and the UI Just Works.

### Sessions live in memory

Acquire sessions are held in an in-memory dict on the worker instance, keyed by UUID. The Streamlit page stores the session ID in `st.session_state`. Single user (the operator on their phone), so v1 needs no durability. SQLite-backed sessions are a v2 concern.

## Status
Accepted

## Consequences

Positive:
- One less subprocess dependency (`uvx`); one less PEP 723 inline script.
- Cancellation, progress, errors become structured API instead of stdout scraping.
- The UI is generic over any source's flow; future ModelScope drops in without UI changes.
- Sessions are stateful across Streamlit reloads (page refresh doesn't lose your place).

Negative:
- `huggingface_hub` is now a hard project dependency, not an inline-script dep. For terminal users who never touch the Streamlit UI, this is a slight regression (was optional). Mitigated by the fact that the project is becoming a real package anyway.
- Session state in memory means a worker restart loses in-progress acquires. Acceptable for single-user; v2 can add SQLite.
- The `AcquireStep` union is verbose. Each step has a distinct shape. A tagged union (pydantic discriminated) is cleaner; v2 may migrate.

Neutral:
- We still depend on `huggingface_hub` being the upstream-supported way to talk to the HF Hub. It is.

## Spec
[spec-002-services-and-acquire](specs/spec-002-services-and-acquire.md)

## Plan
[plan-002-services-and-acquire](plans/plan-002-services-and-acquire.md)
