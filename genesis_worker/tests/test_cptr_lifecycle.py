"""Tests for the cptr lifecycle (tmux session mgmt + HTTP root probe)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from genesis_worker.contracts import ServiceState
from genesis_worker.services.cptr import lifecycle

# --- helpers ---------------------------------------------------------------


def _fake_run_factory(*, returncodes: list[int] | None = None, stdout: str = "") -> callable:  # type: ignore[no-untyped-def]
    """Return a fake ``subprocess.run`` that rotates through returncodes.

    ``returncodes[i]`` is returned for the i-th call. Defaults to all-zero.
    Each call writes the same ``stdout`` (uv tool list, json, etc.).
    """
    codes = returncodes or [0]
    idx = {"n": 0}

    def _fake(args, **kw):  # type: ignore[no-untyped-def]
        rc = codes[idx["n"] % len(codes)]
        idx["n"] += 1
        return subprocess.CompletedProcess(
            args=args, returncode=rc, stdout=stdout, stderr=""
        )

    return _fake


# --- is_running ------------------------------------------------------------


def test_is_running_true_when_session_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(returncodes=[0]))
    assert lifecycle.is_running("cptr") is True


def test_is_running_false_when_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(returncodes=[1]))
    assert lifecycle.is_running("cptr") is False


# --- wait_ready / probe ----------------------------------------------------


def test_probe_host_translates_wildcards() -> None:
    assert lifecycle._probe_host("0.0.0.0") == "127.0.0.1"  # noqa: SLF001
    assert lifecycle._probe_host("::") == "127.0.0.1"  # noqa: SLF001
    assert lifecycle._probe_host("10.0.0.5") == "10.0.0.5"  # noqa: SLF001


def test_wait_ready_returns_true_on_first_200() -> None:
    """Once the probe returns 200, we stop polling."""
    with patch.object(lifecycle, "_probe_root", return_value=True):
        assert lifecycle.wait_ready("127.0.0.1", 4321, timeout_s=5.0) is True


def test_wait_ready_returns_false_on_timeout() -> None:
    with patch.object(lifecycle, "_probe_root", return_value=False):
        assert lifecycle.wait_ready("127.0.0.1", 4321, timeout_s=0.1) is False


def test_probe_root_returns_false_on_connection_error() -> None:
    """The probe must not raise — any error path is 'not ready'."""
    import urllib.error as _ue
    import urllib.request

    def _raise(*a, **kw):  # type: ignore[no-untyped-def]
        raise _ue.URLError("refused")

    with patch.object(urllib.request, "urlopen", _raise):
        assert lifecycle._probe_root("127.0.0.1", 4321) is False  # noqa: SLF001


# --- status ---------------------------------------------------------------


def test_status_stopped_when_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(returncodes=[1]))
    s = lifecycle.status("cptr", "0.0.0.0", 4321)
    assert s.state == ServiceState.STOPPED
    assert s.endpoint == "http://127.0.0.1:4321/"


def test_status_starting_when_session_present_but_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(returncodes=[0]))
    with patch.object(lifecycle, "_probe_root", return_value=False):
        s = lifecycle.status("cptr", "0.0.0.0", 4321)
    assert s.state == ServiceState.STARTING


def test_status_running_when_session_present_and_probe_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(returncodes=[0]))
    with patch.object(lifecycle, "_probe_root", return_value=True):
        s = lifecycle.status("cptr", "0.0.0.0", 4321)
    assert s.state == ServiceState.RUNNING


# --- start_cptr ------------------------------------------------------------


def test_start_cptr_refuses_missing_binary(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-cptr"
    r = lifecycle.start_cptr(
        binary=missing,
        host="0.0.0.0",
        port=4321,
        session_name="cptr",
        log_file=tmp_path / "log",
        health_timeout_s=0.1,
    )
    assert r.ok is False
    assert "binary not found" in r.message


def test_start_cptr_refuses_tmux_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "cptr"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    # First call: has-session (no prior session) → rc=1, kills nothing.
    # Second call: tmux new-session → rc=1, simulate failure.
    monkeypatch.setattr(
        subprocess, "run", _fake_run_factory(returncodes=[1, 1], stdout="boom")
    )
    r = lifecycle.start_cptr(
        binary=binary,
        host="0.0.0.0",
        port=4321,
        session_name="cptr",
        log_file=tmp_path / "log",
        health_timeout_s=0.1,
    )
    assert r.ok is False
    assert "tmux new-session failed" in r.message


def test_start_cptr_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "cptr"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    # has-session → rc=1 (no prior session); new-session → rc=0.
    monkeypatch.setattr(
        subprocess, "run", _fake_run_factory(returncodes=[1, 0])
    )
    with patch.object(lifecycle, "wait_ready", return_value=True):
        r = lifecycle.start_cptr(
            binary=binary,
            host="0.0.0.0",
            port=4321,
            session_name="cptr",
            log_file=tmp_path / "log",
            health_timeout_s=10.0,
        )
    assert r.ok is True


def test_start_cptr_timeout_returns_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "cptr"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setattr(
        subprocess, "run", _fake_run_factory(returncodes=[1, 0])
    )
    with patch.object(lifecycle, "wait_ready", return_value=False):
        r = lifecycle.start_cptr(
            binary=binary,
            host="0.0.0.0",
            port=4321,
            session_name="cptr",
            log_file=tmp_path / "log",
            health_timeout_s=0.1,
        )
    assert r.ok is False
    assert "did not become ready" in r.message


def test_start_cptr_kills_prior_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A previous session of the same name is killed before we start."""
    binary = tmp_path / "cptr"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    calls: list[list[str]] = []
    # First call (has-session) → rc=0 (prior session present).
    # Subsequent calls (kill-session, new-session) → rc=0.
    rc_cycle = [0, 0, 0]
    idx = {"n": 0}

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        rc = rc_cycle[min(idx["n"], len(rc_cycle) - 1)]
        idx["n"] += 1
        return subprocess.CompletedProcess(
            args=args, returncode=rc, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with patch.object(lifecycle, "wait_ready", return_value=True):
        lifecycle.start_cptr(
            binary=binary,
            host="0.0.0.0",
            port=4321,
            session_name="cptr",
            log_file=tmp_path / "log",
            health_timeout_s=10.0,
        )
    cmds = [" ".join(c[:3]) for c in calls]
    assert "tmux has-session" in cmds[0]
    assert "tmux kill-session" in cmds[1]
    assert "tmux new-session" in cmds[2]


# --- stop_cptr -------------------------------------------------------------


def test_stop_cptr_no_session_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(returncodes=[1]))
    r = lifecycle.stop_cptr("cptr")
    assert r.ok is True
    assert "no session" in r.message


def test_stop_cptr_graceful_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session vanishes after Ctrl-C; we report success without a hard kill."""
    call_count = {"n": 0}

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        # First call: has-session → rc=0 (session present).
        # Subsequent calls: has-session → rc=1 (gone after C-c).
        return subprocess.CompletedProcess(
            args=args, returncode=0 if call_count["n"] == 1 else 1, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    r = lifecycle.stop_cptr("cptr")
    assert r.ok is True
    assert "killed cptr" in r.message


def test_stop_cptr_force_kill_when_graceful_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session still present after the wait window → hard kill."""
    # All has-session calls return 0 (always present); kill-session at the end.
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(returncodes=[0]))
    r = lifecycle.stop_cptr("cptr")
    assert r.ok is True
    assert "forced" in r.message