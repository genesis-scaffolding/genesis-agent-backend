# ADR-024: `utils/process/docker.py` — Docker process utility

## Title

Framework-provided Docker process utility, parallel to `utils/process/tmux.py`.

## Status

Accepted.

## Context

ADR-013 introduced `genesis_worker/utils/process/tmux.py` as a framework utility — stateless tmux session management — so that services compose lifecycle primitives instead of reimplementing tmux orchestration. Both in-tree services (`llama_swap`, `cptr`) now use `TmuxProcess` for their lifecycle.

The ComfyUI service (ADR-025) runs as a Docker container, not a tmux session. Without a parallel utility, the service would have to shell out to `docker run`, `docker stop`, `docker inspect`, `docker pull`, and `docker logs` directly — and parse layer progress from `docker pull`'s stderr inline. The same primitives will be reused by every future container-based service (A1111, Fooocus, Kohya, …), so the duplication argument that justified `TmuxProcess` applies here.

There is also a precedent for separating *install* from *lifecycle* on the service side: ADR-012 puts acquisition in `installs.py` / `install.py` per service, and the per-service code calls the GitHub tarball backend for fetching. For ComfyUI the equivalent backend is "list tags + pull from GHCR". Both belong on the utility — not as a service-specific install helper, but as reusable Docker plumbing the service composes.

We considered wrapping `docker run` in `TmuxProcess` (treating the docker daemon as a child process) and overriding `_kill_children` to call `docker stop`. We rejected it: the tmux session adds nothing here. The container itself is the long-running process; tmux would only host the `docker run` invocation, which exits immediately after detaching the container. There is no child to drain, no shell pipeline to interrupt. The cleaner abstraction is direct Docker control.

## Decision

We will add a new utility module `genesis_worker/utils/process/docker.py` exposing `DockerContainer` and a few static helpers. The module is a leaf in `utils/` and follows ADR-013's structural conventions.

### Shape

```python
class DockerContainer:
    """Lifecycle for one Docker container by name."""

    def __init__(self, name: str) -> None: ...

    # lifecycle (mirrors TmuxProcess)
    def is_running(self) -> bool: ...
    def run(
        self,
        *,
        image: str,
        command: list[str] | None = None,
        ports: dict[str, tuple[str, int]] | None = None,   # container_port -> (host_ip, host_port)
        volumes: dict[str, str] | None = None,             # container_path -> host_path (read-write by default)
        env: dict[str, str] | None = None,
        runtime: str | None = None,                          # "nvidia"
        gpu_flags: list[str] | None = None,                  # e.g. ["driver=nvidia", "count=1"]
        hostname: str | None = None,
        restart: str = "unless-stopped",
        extra_args: list[str] | None = None,                 # appended to `docker run`
    ) -> StartResult: ...
    def stop(self, *, timeout_s: float = 30.0) -> StopResult: ...
    def remove(self) -> None: ...                            # `docker rm` after stop

    # install backend (mirrors GithubReleaseTarball)
    @staticmethod
    def image_present(image: str) -> bool: ...
    @staticmethod
    def list_local_tags(repo: str) -> list[str]: ...
    @staticmethod
    def list_remote_tags(repo: str, *, auth_token: str | None = None) -> list[str]: ...
    @staticmethod
    def pull(
        image: str,
        *,
        progress: Callable[[str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        timeout_s: float = 1800.0,
    ) -> None: ...

    # observability
    def logs(self, tail_lines: int = 200) -> str: ...

    # environment probes
    @staticmethod
    def docker_available() -> bool: ...
    @staticmethod
    def nvidia_runtime_available() -> bool: ...
```

`run()` returns a `StartResult` whose `ok=True` indicates the container was created and `docker inspect` confirms `State.Running`. The plugin's `start()` should additionally call `wait_ready()` against its health probe; the framework does not fold readiness into the run result because readiness depends on the service's probe path.

### Subprocess conventions

- All `docker` invocations go through `subprocess.run` with `check=False`, `capture_output=True`, `text=True`. Exit-code interpretation lives in the utility; callers receive dataclasses.
- Errors that the utility surfaces to callers: docker missing (`docker_available()` returns False at the service layer), image pull authentication failures (raised as `RuntimeError`), container already exists (handled by `docker rm -f` before `run`).
- `pull()` streams stderr line-by-line. Each non-empty line is forwarded to `progress(line)`. The function checks `cancel()` between lines. Docker's progress format is line-oriented JSON (`{"status": "...", "id": "...", "progressDetail": {...}}`) on modern Docker; the utility forwards raw lines and lets the install session decide what to publish.

### GHCR tag listing

`list_remote_tags` hits the public GHCR v2 endpoint:

```
GET https://ghcr.io/token?service=ghcr.io&scope=repository:genesis-scaffolding/comfyui-cuda:pull
GET https://ghcr.io/v2/genesis-scaffolding/comfyui-cuda/tags/list
```

Token cache: in-process for the duration of one `available_versions()` call. Persistence to disk is the installable's concern (mirrors `GithubReleaseTarball._read_release_cache`).

### Why not the Docker Python SDK?

`docker` (the PyPI package) wraps the daemon over a Unix socket and parses JSON natively. We considered it. We rejected it because:

- It adds a dependency to the framework that nothing else needs.
- The CLI surface (`docker run`, `docker stop`, `docker pull`, `docker inspect`, `docker logs`) is stable and well-understood; shelling out to it is what the rest of the worker already does for `tmux`, `nvidia-smi`, `pkill`, `uv`, and `git`.
- Subprocess mocking for tests is uniform with the rest of the codebase (`monkeypatch.setattr(subprocess, "run", ...)`).

### Where this lives

```
genesis_worker/
  utils/
    process/
      __init__.py     # adds `DockerContainer` to exports
      tmux.py         # unchanged
      docker.py       # NEW
```

`utils/process/__init__.py` re-exports `DockerContainer` alongside `TmuxProcess`.

## Consequences

**Positive**

- The ComfyUI service (ADR-025) composes `DockerContainer` instead of shelling to `docker` directly; the install/lifecycle/observability primitives are tested in one place.
- Future container-based services (A1111, Fooocus, Kohya) get the same primitives with no new framework code.
- The utility's API is small and mirrors `TmuxProcess`, so a maintainer who knows one learns the other quickly.

**Negative**

- Shelling out to `docker` is slower than calling the Docker SDK directly. Each call is a fork+exec. For the lifecycle operations this is invisible (one call per start/stop). For `logs()` polling in `tail_log()`, the cost is paid every refresh interval; a fragment running at 2 s × ~50 ms is still under the user's perception threshold.
- `pull()` progress parsing relies on Docker's stderr format. Newer Docker versions emit structured JSON; older emit human-readable lines. We forward raw lines to `progress()` and let the install session decide. If Docker changes its format incompatibly, only the install session's progress parser breaks, not the utility.

**Neutral**

- `DockerContainer` is a thin wrapper. It does not own any state besides the container name; the installable owns image selection, the service owns compose args, the symlink applier (ADR-025) owns bind-mount contents.
- `pull()` is a static method; there's no `DockerPull` class. This matches `GithubReleaseTarball.available_versions()` being a method on the backend object — the install session carries state, not the pull helper.


