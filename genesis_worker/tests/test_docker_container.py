"""Tests for ``DockerContainer`` — mocked subprocess, no real docker invocations."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from genesis_worker.utils.process.docker import DockerContainer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed(
    args: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    """Build a CompletedProcess as if subprocess.run returned it."""
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, *, respond: Any) -> None:
    """Replace ``subprocess.run`` with ``respond`` (callable or list of responses).

    When ``respond`` is a callable, every call returns ``respond(args, **kw)``.
    When ``respond`` is a list, each call pops the next entry (or returns
    a rc=0 no-op when the list is exhausted).
    """
    if callable(respond):

        def _runner(args, **kw):  # type: ignore[no-untyped-def]
            return respond(args, **kw)

        monkeypatch.setattr(subprocess, "run", _runner)
        return

    responses = list(respond)
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        if responses:
            r = responses.pop(0)
            if isinstance(r, BaseException):
                raise r
            return r
        return _completed(list(args), returncode=0)

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)
    return calls  # type: ignore[return-value]


# --- is_running ------------------------------------------------------------


def test_is_running_true_when_inspect_reports_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=0, stdout="true\n"),
    )
    assert DockerContainer("c1").is_running() is True


def test_is_running_false_when_inspect_reports_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=0, stdout="false\n"),
    )
    assert DockerContainer("c1").is_running() is False


def test_is_running_false_when_container_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """docker inspect returns non-zero when the container doesn't exist."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=1, stderr="No such container"),
    )
    assert DockerContainer("c1").is_running() is False


# --- run ------------------------------------------------------------------


def test_run_calls_remove_first_then_docker_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=0, stdout="container-id\n")

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    result = DockerContainer("c1").run(
        image="ghcr.io/genesis-scaffolding/comfyui-cuda:v0.34.0-cuda-13.0-amd64",
        command=["--verbose"],
    )
    assert result.ok is True
    assert calls[0][:3] == ["docker", "rm", "-f"]
    assert calls[0][3] == "c1"
    run_call = calls[1]
    assert run_call[0:2] == ["docker", "run"]
    assert "-d" in run_call
    assert "--name" in run_call
    assert "c1" in run_call
    assert "--restart" in run_call
    assert "unless-stopped" in run_call
    assert run_call[-2:] == ["ghcr.io/genesis-scaffolding/comfyui-cuda:v0.34.0-cuda-13.0-amd64", "--verbose"]


def test_run_passes_ports_volumes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    DockerContainer("c1").run(
        image="img:latest",
        ports={"8188/tcp": ("0.0.0.0", 8188)},
        volumes={"/data/models": "/host/models"},
        env={"PUID": "1000", "PGID": "1000"},
    )
    argv = calls[1]
    assert "-p" in argv
    assert argv[argv.index("-p") + 1] == "0.0.0.0:8188:8188/tcp"
    assert "-v" in argv
    assert argv[argv.index("-v") + 1] == "/host/models:/data/models"
    assert "-e" in argv
    e_idx = argv.index("-e")
    assert argv[e_idx + 1] == "PUID=1000"


def test_run_passes_runtime_and_gpus_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    DockerContainer("c1").run(
        image="img:latest",
        runtime="nvidia",
        gpu_flags=["driver=nvidia", "count=1"],
    )
    argv = calls[1]
    assert "--runtime" in argv
    assert argv[argv.index("--runtime") + 1] == "nvidia"
    assert "--gpus" in argv
    assert argv[argv.index("--gpus") + 1] == "driver=nvidia,count=1"


def test_run_passes_shm_size_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    DockerContainer("c1").run(image="img:latest", shm_size="1g")
    argv = calls[1]
    assert "--shm-size" in argv
    assert argv[argv.index("--shm-size") + 1] == "1g"


def test_run_omits_shm_size_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    DockerContainer("c1").run(image="img:latest")
    assert "--shm-size" not in calls[1]


def test_run_returns_failure_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=125, stderr="port already allocated"),
    )
    result = DockerContainer("c1").run(image="img:latest")
    assert result.ok is False
    assert "port already allocated" in result.message


# --- stop ------------------------------------------------------------------


def test_stop_calls_docker_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        # First call is is_running probe (inspect) → true
        if "inspect" in args:
            return _completed(args, returncode=0, stdout="true\n")
        return _completed(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    result = DockerContainer("c1").stop()
    assert result.ok is True
    assert any("stop" in args for args in calls)


def test_stop_falls_back_to_kill_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        if "inspect" in args:
            return _completed(args, returncode=0, stdout="true\n")
        if "stop" in args and "kill" not in args:
            return _completed(args, returncode=1, stderr="timeout")
        return _completed(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    result = DockerContainer("c1").stop(timeout_s=5)
    assert result.ok is True
    assert any("kill" in args for args in calls)
    assert "forced" in result.message


def test_stop_is_noop_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the container isn't running, stop() returns ok with 'no container'."""
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=1, stderr="No such container")

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    result = DockerContainer("c1").stop()
    assert result.ok is True
    assert "no container" in result.message
    # Only the inspect call happened; no docker stop was issued.
    assert not any("stop" in args for args in calls if "inspect" not in args)


# --- remove ----------------------------------------------------------------


def test_remove_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """docker rm -f on a missing container returns non-zero; we swallow it."""
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=1, stderr="No such container")

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    DockerContainer("c1").remove()  # must not raise
    assert any(args[:3] == ["docker", "rm", "-f"] for args in calls)


# --- logs ------------------------------------------------------------------


def test_logs_returns_combined_stdout_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=0, stdout="out-1\n", stderr="err-1\n"),
    )
    out = DockerContainer("c1").logs(tail_lines=10)
    assert "out-1" in out
    assert "err-1" in out


def test_logs_empty_when_container_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=1, stderr="No such container"),
    )
    assert DockerContainer("c1").logs() == ""


# --- image_present ---------------------------------------------------------


def test_image_present_true_when_inspect_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kw: _completed(args, returncode=0, stdout="[]")
    )
    assert DockerContainer.image_present("img:1") is True


def test_image_present_false_when_inspect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kw: _completed(args, returncode=1, stderr="not found")
    )
    assert DockerContainer.image_present("img:1") is False


# --- list_local_tags -------------------------------------------------------


def test_list_local_tags_parses_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        "ghcr.io/genesis-scaffolding/comfyui-cuda:v0.34.0-cuda-13.0-amd64\n"
        "ghcr.io/genesis-scaffolding/comfyui-cuda:v0.33.0-cuda-12.8-amd64\n"
        "ghcr.io/genesis-scaffolding/comfyui-cuda:latest\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=0, stdout=body))
    tags = DockerContainer.list_local_tags("ghcr.io/genesis-scaffolding/comfyui-cuda")
    assert tags == [
        "v0.34.0-cuda-13.0-amd64",
        "v0.33.0-cuda-12.8-amd64",
        "latest",
    ]


def test_list_local_tags_empty_on_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=1))
    assert DockerContainer.list_local_tags("repo/x") == []


def test_list_local_tags_filters_unrelated_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        "ghcr.io/genesis-scaffolding/comfyui-cuda:v0.34.0-cuda-13.0-amd64\n"
        "other/repo:latest\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=0, stdout=body))
    tags = DockerContainer.list_local_tags("ghcr.io/genesis-scaffolding/comfyui-cuda")
    assert tags == ["v0.34.0-cuda-13.0-amd64"]


# --- list_remote_tags (GHCR) ----------------------------------------------


def _patch_ghcr(monkeypatch: pytest.MonkeyPatch, *, tags: list[str]) -> None:
    """Patch urllib + the GHCR fetch path to return canned responses."""
    responses = iter(
        [
            {"token": "fake-token"},  # token endpoint
            {"name": "repo", "tags": tags},  # tags/list endpoint
        ]
    )

    def _fake_urlopen(req, **kw):  # type: ignore[no-untyped-def]

        body = json.dumps(next(responses)).encode()

        class _Resp:
            def __init__(self, body: bytes) -> None:
                self._body = body

            def __enter__(self) -> _Resp:  # noqa: PYI034
                return self

            def __exit__(self, *a: object) -> None:
                pass

            def read(self) -> bytes:
                return self._body

        return _Resp(body)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)


def test_list_remote_tags_fetches_token_then_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ghcr(monkeypatch, tags=["v0.34.0-cuda-13.0-amd64", "v0.33.0-cuda-12.8-amd64"])
    tags = DockerContainer.list_remote_tags("ghcr.io/genesis-scaffolding/comfyui-cuda")
    assert tags == ["v0.34.0-cuda-13.0-amd64", "v0.33.0-cuda-12.8-amd64"]


def test_list_remote_tags_uses_provided_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``auth_token`` is supplied, the token endpoint is not called."""
    seen_urls: list[str] = []

    def _fake_urlopen(req, **kw):  # type: ignore[no-untyped-def]
        seen_urls.append(req.full_url)
        body = json.dumps({"name": "repo", "tags": ["v1"]}).encode()

        class _Resp:
            def __enter__(self) -> _Resp:  # noqa: PYI034
                return self

            def __exit__(self, *a: object) -> None:
                pass

            def read(self) -> bytes:
                return body

        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    tags = DockerContainer.list_remote_tags("repo/x", auth_token="explicit")
    assert tags == ["v1"]
    assert len(seen_urls) == 1
    assert seen_urls[0].endswith("/v2/repo/x/tags/list")


def test_list_remote_tags_returns_empty_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_urlopen(req, **kw):  # type: ignore[no-untyped-def]
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert DockerContainer.list_remote_tags("repo/x") == []


# --- pull ------------------------------------------------------------------


def test_pull_streams_stderr_lines_to_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each stderr line is forwarded to ``progress`` in order."""
    seen: list[str] = []
    stderr = (
        "v0.34.0-cuda-13.0-amd64: Pulling fs layer\n"
        "v0.34.0-cuda-13.0-amd64: Downloading  100MB / 200MB\n"
        "v0.34.0-cuda-13.0-amd64: Pull complete\n"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=0, stdout="", stderr=stderr),
    )
    DockerContainer.pull("img:1", progress=seen.append)
    assert len(seen) == 3
    assert "Pulling fs layer" in seen[0]
    assert "Pull complete" in seen[2]


def test_pull_skips_empty_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty / whitespace-only lines are not forwarded to progress."""
    seen: list[str] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=0, stdout="", stderr="\n\nfoo\n\nbar\n\n"),
    )
    DockerContainer.pull("img:1", progress=seen.append)
    assert seen == ["foo", "bar"]


def test_pull_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=1, stderr="manifest unknown"),
    )
    with pytest.raises(RuntimeError, match="manifest unknown"):
        DockerContainer.pull("img:1")


def test_pull_drops_progress_flag_when_docker_too_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``docker`` is < 23.0 and rejects --progress, we silently fall back."""
    # Pretend the version probe already cached "no JSON support".
    from genesis_worker.utils.process import docker as docker_mod

    docker_mod._PROGRESS_SUPPORT_CACHE["json"] = False
    try:
        seen_argv: list[list[str]] = []

        def _run(args, **kw):  # type: ignore[no-untyped-def]
            seen_argv.append(list(args))
            return _completed(args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        DockerContainer.pull("img:1")
        assert seen_argv == [["docker", "pull", "img:1"]]
    finally:
        docker_mod._PROGRESS_SUPPORT_CACHE.pop("json", None)


def test_pull_passes_progress_flag_when_docker_supports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``docker`` is 23.0+, we pass ``--progress=json``."""
    from genesis_worker.utils.process import docker as docker_mod

    docker_mod._PROGRESS_SUPPORT_CACHE["json"] = True
    try:
        seen_argv: list[list[str]] = []

        def _run(args, **kw):  # type: ignore[no-untyped-def]
            seen_argv.append(list(args))
            return _completed(args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        DockerContainer.pull("img:1", progress_format="json")
        assert seen_argv == [["docker", "pull", "--progress=json", "img:1"]]
    finally:
        docker_mod._PROGRESS_SUPPORT_CACHE.pop("json", None)


def test_pull_plain_format_never_adds_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``progress_format="plain"`` should never include the flag, even when JSON is supported."""
    from genesis_worker.utils.process import docker as docker_mod

    docker_mod._PROGRESS_SUPPORT_CACHE["json"] = True
    try:
        seen_argv: list[list[str]] = []

        def _run(args, **kw):  # type: ignore[no-untyped-def]
            seen_argv.append(list(args))
            return _completed(args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        DockerContainer.pull("img:1", progress_format="plain")
        assert seen_argv == [["docker", "pull", "img:1"]]
    finally:
        docker_mod._PROGRESS_SUPPORT_CACHE.pop("json", None)


def test_pull_aborts_when_cancel_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``cancel`` returns True between lines, pull raises _Canceled."""
    from genesis_worker.utils.process.docker import _Canceled

    cancel_after = 2

    class _Cancel:
        def __init__(self) -> None:
            self.count = 0

        def __call__(self) -> bool:
            self.count += 1
            return self.count > cancel_after

    # Use a real subprocess.run mock that returns four lines on stdout.
    # The real pull loop iterates merged lines, checking cancel before
    # forwarding each. ``cancel_after=2`` means cancel() returns False
    # once, True from the second call onward, so exactly one line
    # reaches progress.
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(
            args,
            returncode=0,
            stdout="line1\nline2\nline3\nline4\n",
            stderr="",
        ),
    )
    seen: list[str] = []
    with pytest.raises(_Canceled):
        DockerContainer.pull("img:1", progress=seen.append, cancel=_Cancel())
    # Two lines reach progress: cancel() returns False on its 1st and
    # 2nd calls, only flips True on the 3rd. By then line1 and line2
    # have been forwarded.
    assert seen == ["line1", "line2"]


# --- environment probes ---------------------------------------------------


def test_docker_available_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=0))
    assert DockerContainer.docker_available() is True


def test_docker_available_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=1))
    assert DockerContainer.docker_available() is False


def test_nvidia_runtime_available_true(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "Runtimes: io.containerd.runc.v2 nvidia runc\n"
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=0, stdout=body))
    assert DockerContainer.nvidia_runtime_available() is True


def test_nvidia_runtime_available_false(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "Runtimes: io.containerd.runc.v2 runc\n"
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=0, stdout=body))
    assert DockerContainer.nvidia_runtime_available() is False


def test_nvidia_runtime_available_false_on_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _completed(args, returncode=1))
    assert DockerContainer.nvidia_runtime_available() is False


# --- exec_run --------------------------------------------------------------


def test_exec_run_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return _completed(args, returncode=0, stdout="checkpoints\nloras\nvae\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _runner)
    monkeypatch.setattr("genesis_worker.utils.process.docker._run", _runner)

    rc, stdout, stderr = DockerContainer("c1").exec_run(["ls", "/models/"])
    assert rc == 0
    assert stdout == "checkpoints\nloras\nvae\n"
    assert stderr == ""
    assert calls[-1] == ["docker", "exec", "c1", "ls", "/models/"]


def test_exec_run_returns_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: _completed(args, returncode=1, stderr="not found"),
    )
    rc, stdout, stderr = DockerContainer("c1").exec_run(["ls", "/nonexistent"])
    assert rc == 1
    assert stdout == ""
    assert stderr == "not found"


def test_exec_run_timeout_returns_minus_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: (_ (_("timeout") if False else None)),  # unreachable; raise instead
    )

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired("cmd", 10)

    monkeypatch.setattr(subprocess, "run", _runner)
    rc, stdout, stderr = DockerContainer("c1").exec_run(["sleep", "999"])
    assert rc == -1


def test_exec_run_uses_timeout_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    def _runner(args, **kw):  # type: ignore[no-untyped-def]
        seen.append(kw.get("timeout", 0))
        return _completed(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _runner)
    DockerContainer("c1").exec_run(["echo", "hi"], timeout_s=7.5)
    assert seen == [7.5]
