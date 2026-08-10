# Plan 002: Services and acquire flows

Implements [spec-002](../specs/spec-002-services-and-acquire.md).

## Working rules

- Branch: `feature/genesis-worker-services` from `master`.
- Continue using `uv` for dependency changes.
- The running llama-swap on `:8080` is **not** stopped. Validation uses a parallel llama-swap on `:8081` (or a separate scratch host).
- `bin/up` is **not modified**. We add a Python equivalent in `services/llama_swap/lifecycle.py` that exercises the same tmux + curl behavior.
- Commit per logical chunk (lifecycle + service, then agent_export, then acquire).

## File-by-file

### Lifecycle and service (chunk 1)

1. **Create branch** `feature/genesis-worker-services`.
2. **`uv add huggingface_hub`** at the repo root.
3. **Extend `genesis_worker/services/_base.py`** with the full set of dataclasses per spec-002 (`ServiceState`, `ServiceCapabilities`, `ServiceResourceEstimate`, `ServiceStatus`, `StartResult`, `StopResult`) and the `InferenceService` Protocol.
4. **Write `genesis_worker/services/llama_swap/lifecycle.py`** per spec-002.
5. **Write `genesis_worker/services/llama_swap/service.py`** per spec-002.
6. **Write `genesis_worker/tests/test_lifecycle.py`** — install a fake `llama-swap` shim under `tmp_path`; prepend `tmp_path` to `PATH` via `monkeypatch.setenv`; have the shim serve a static `/v1/models` response via `python3 -m http.server` on a free port; test start → wait_ready → status==RUNNING → stop → status==STOPPED. Pick the free port programmatically (bind a socket, get the port, close, use that port).
7. **Write `genesis_worker/tests/test_service_llama_swap.py`** per spec-002.
8. **`uv run pytest genesis_worker/tests/`** — must pass.
9. **Parallel lifecycle validation against the real llama-swap binary:**
   - Identify a free port (e.g. `8081`).
   - In a scratch terminal: `cd /tmp && PATH=$HOME/.local/bin:$PATH uv run python -c "from genesis_worker.settings import Settings; from genesis_worker.services.llama_swap.lifecycle import start_swap, status, stop_swap; from pathlib import Path; s = Settings(); s.services.llama_swap.listen_addr='127.0.0.1:8081'; print(start_swap(Path('config.yaml'), '127.0.0.1:8081', 'swap-test', Path('/tmp/swap-test.log'), 60.0))"`.
   - Confirm `curl -s http://127.0.0.1:8081/v1/models` returns the same model list as `:8080`.
   - `tmux kill-session -t swap-test`. The running `:8080` is unaffected.
10. **Commit chunk 1.**

### Agent export (chunk 2)

11. **Write `genesis_worker/services/llama_swap/export_pi_config.py`** per spec-002.
12. **Write `genesis_worker/tests/test_export_pi_config.py`** per spec-002. Use the current `config.yaml` and the current `pi-models.json` as fixtures; assert field-by-field equivalence.
13. **Run a real-fixture diff:**
    ```bash
    uv run python -c "
    from genesis_worker.services.llama_swap.export_pi_config import build_provider
    from pathlib import Path
    import json
    print(json.dumps(build_provider(Path('config.yaml')), sort_keys=True, indent=2))
    " > /tmp/new-pi-models.json
    diff <(jq -S . pi-models.json) <(jq -S . /tmp/new-pi-models.json)
    ```
    Should show no semantic differences.
14. **`uv run pytest`** — must pass.
15. **Commit chunk 2.**

### Acquire flows (chunk 3)

16. **Extend `genesis_worker/sources/_base.py`** with the `AcquireStep`, `AcquireFileGroup`, `AcquireProgress`, `AcquireChoice`, `AcquireState`, and `AcquireSession` definitions per spec-002.
17. **Extend `genesis_worker/sources/huggingface.py`** with `HfAcquireSession`:
    - Constructor takes an `HfApi`, an `AcquireState`, and a `cache_dir`.
    - `current_step()` returns the last computed step or an initial `inspecting` step.
    - `submit(choice)` advances the state machine:
      - From `inspecting` (after `current_step` was called once with files populated) → `select_files`.
      - From `select_files` (with a valid `main_index`) → `confirm_storage`.
      - From `confirm_storage` (with `confirm=True`) → `downloading`, then runs `snapshot_download` (or per-file `hf_hub_download`) in a `threading.Thread`; on completion transitions to `complete`; on error to `failed`.
      - On `cancel()`, set the cancel event; the running thread checks the event between files and aborts; final step is `cancelled`.
    - Use `huggingface_hub`'s logging callback to capture the last N log lines into `log_tail`.
    - Aggregate file-byte counts into `AcquireProgress` via a shared counter updated as files complete.
18. **Write `genesis_worker/tests/test_acquire_hf.py`** — mock `HfApi.list_repo_tree` to return a canned response (2 main GGUFs, 1 mmproj). Mock `snapshot_download` (or `hf_hub_download`) with a stub that records calls. Drive the session: `current_step()` (triggers inspecting) → `submit(AcquireChoice(main_index=1, aux_indexes=[3]))` → `current_step()` (should be `confirm_storage`) → `submit(AcquireChoice(confirm=True))` → poll `current_step()` until `complete`. Assert the stub was called with the right `allow_patterns` and `cache_dir`. **No real network I/O.**
19. **`uv run pytest`** — must pass.
20. **`uv run ruff check`, `uv run pyright`** — must exit 0.
21. **`make all`** — must still pass; on-disk artifacts unchanged.
22. **Confirm the running llama-swap** on `:8080` is still serving.
23. **Wait for user approval**, then commit chunks 2 and 3 (or commit each chunk separately as work proceeds).

## Notes

- The fake `llama-swap` shim in `test_lifecycle.py` is a small bash or Python script that mimics `llama-swap`'s behavior enough for our tests (responds 200 to `/v1/models` with a static JSON body). It is added to the test fixture; not committed as a project file.
- Cancellation of `snapshot_download` is not natively supported; the implementation uses a `threading.Event` polled between files. If the library later adds a token parameter, swap to that. Either way, the `AcquireSession.cancel()` semantics are honored.
- The agent-export diff uses `jq -S` to canonicalize JSON before diffing; this catches ordering and whitespace differences while highlighting real semantic changes.
- Real-network end-to-end HF acquire testing happens during Phase 9 from the phone browser. The unit tests cover the state machine and the library calls; integration is left to the manual scenario.
- The chunks (1, 2, 3) are independently shippable. The first chunk alone (lifecycle + service) is useful without the rest.
