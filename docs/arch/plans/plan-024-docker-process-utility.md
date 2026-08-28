# Plan 024: `utils/process/docker.py` — Docker process utility

Implements [ADR-024](../adr-024-docker-process-utility.md). Phase 2 of the ComfyUI rollout; lands after Phase 1 (`vault_path`) is merged.

## Working rules

- Branch: `feature/comfyui-service` off `main`.
- One commit per plan.
- Verification gate:
  ```
  uv run pytest -q
  uv run pyright
  uv run ruff check genesis_worker
  ```
- All docker calls are mocked in tests; no real `docker` invocations from the test suite.

---

## Step 1 — Create `genesis_worker/utils/process/docker.py`

Implement `DockerContainer` per ADR-024.

### Subprocess conventions

- All `docker` invocations: `subprocess.run([...], check=False, capture_output=True, text=True)`.
- `run()` builds the argv list from kwargs; never `shell=True`.
- Errors surface as `StartResult`/`StopResult(ok=False, message=stderr)` or raise `RuntimeError(stderr)`.
- `run()` first calls `remove()` to drop any prior container of the same name (idempotent).

### API surface

- `is_running() -> bool` — parses `{{.State.Running}}` from `docker inspect`.
- `run(*, image, command, ports, volumes, env, runtime, gpu_flags, hostname, restart) -> StartResult`.
- `stop(*, timeout_s=30) -> StopResult` — `docker stop`, fallback to `docker kill` on timeout.
- `remove() -> None` — `docker rm`; idempotent (no error when absent).
- `@staticmethod image_present(image) -> bool` — `docker image inspect <image>` rc==0.
- `@staticmethod list_local_tags(repo) -> list[str]` — parses `docker images --format '{{.Repository}}:{{.Tag}}' <repo>`.
- `@staticmethod list_remote_tags(repo, *, auth_token=None) -> list[str]` — two-step GHCR: token endpoint → `/v2/<repo>/tags/list`. Token cache is in-process only; persistence is the installable's concern.
- `@staticmethod pull(image, *, progress, cancel, timeout_s=1800) -> None` — streams stderr line-by-line; calls `progress(line)` per non-empty line; checks `cancel()` between lines.
- `logs(tail_lines=200) -> str` — `docker logs --tail N name`, returns stdout.
- `@staticmethod docker_available() -> bool` — `docker version` exits 0.
- `@staticmethod nvidia_runtime_available() -> bool` — `docker info` mentions nvidia runtime.

### Notes

- `pull()` forwards raw stderr lines to `progress()` — the install session decides what to publish as `AcquireStep.title`. Don't parse Docker's progress format inside the utility.
- `list_remote_tags()` returns a simple `list[str]`. The installable maps each tag into `InstallVersion` with `url=f"ghcr.io/{repo}:{tag}"`, `sha256=None`, `size_bytes=None` (deferred per ADR-024).
- `DockerContainer.run()` always pre-clears any prior container of the same name. This matches `docker-compose down && up` semantics — "Start always works".

## Step 2 — Update `genesis_worker/utils/process/__init__.py`

```python
"""Process management helpers — tmux, docker."""

from .docker import DockerContainer
from .tmux import TmuxProcess

__all__ = ["DockerContainer", "TmuxProcess"]
```

## Step 3 — Tests

Add `genesis_worker/tests/test_docker_container.py`. Mock `subprocess.run` (the convention used by `test_tmux_process.py` and others) and assert:

- `is_running()` correctly parses `{{.State.Running}}` for true / false / missing-container cases.
- `run()` translates kwargs to the expected `docker run -d --name ...` argv; rc==0 returns `StartResult(ok=True)`; rc≠0 returns `StartResult(ok=False, message=stderr)`.
- `run()` calls `remove()` first (idempotent), so `docker rm` is always invoked before `docker run`.
- `stop()` issues `docker stop <name>` with timeout; falls back to `docker kill` when stop times out.
- `remove()` is a no-op when the container is absent.
- `image_present(image)` returns True iff `docker image inspect <image>` exits 0.
- `list_local_tags(repo)` parses `docker images --format ... <repo>` output line-by-line; ignores empty lines and headers.
- `list_remote_tags(repo)` does the two-step GHCR fetch; the right URLs are hit (mock the token endpoint and tags endpoint with canned JSON); tags list returned matches the body.
- `pull(image)` streams each stderr line to `progress(line)`; `cancel()` returning True between lines raises the cancellation sentinel.
- `logs(tail_lines)` calls `docker logs --tail N name` and returns stdout.
- `docker_available()` returns True on rc==0, False otherwise.
- `nvidia_runtime_available()` greps `docker info` output for the nvidia runtime string.

## Step 4 — Run gate

```
uv run pytest -q
uv run pyright
uv run ruff check genesis_worker
```

All must pass. Commit and pause for user approval before Phase 3.

---

## Files changed summary

| File | Change |
|---|---|
| `genesis_worker/utils/process/docker.py` | Create |
| `genesis_worker/utils/process/__init__.py` | Add `DockerContainer` export |

## Notes

- No real `docker` binary required to run the tests — every call is mocked. CI runs these on any host.
- `pull()`'s progress format is intentionally raw. Docker versions emit different shapes; the install session (plan-025) is the right place to format.
- Future container services (A1111, Fooocus, Kohya) compose this utility without further framework changes.
