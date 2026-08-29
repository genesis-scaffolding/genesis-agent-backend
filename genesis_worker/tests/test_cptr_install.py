"""Tests for the cptr installable (uv tool install backend)."""

from __future__ import annotations

import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from genesis_worker.contracts import AcquireChoice, AcquireView, InstallState
from genesis_worker.services.cptr.install import (
    CptrInstall,
    _uv_tool_installed_version,
)

# --- helpers ---------------------------------------------------------------


def _fake_pypi_payload(version: str = "0.9.21", size: int = 4_560_844) -> dict:
    """A minimal PyPI JSON response with one wheel."""
    return {
        "info": {
            "name": "cptr",
            "version": version,
            "package_url": f"https://pypi.org/project/cptr/{version}/",
        },
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "url": f"https://files.example/cptr-{version}-py3-none-any.whl",
                "size": size,
                "digests": {"sha256": "deadbeef" * 8},
            }
        ],
    }


# --- _uv_tool_installed_version -------------------------------------------


def test_uv_tool_list_parses_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parser reads the second whitespace token and strips the leading v."""
    fake = subprocess.CompletedProcess(
        args=["uv", "tool", "list"],
        returncode=0,
        stdout="cptr v0.9.21\n- cptr\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert _uv_tool_installed_version("cptr", timeout=5.0) == "0.9.21"


def test_uv_tool_list_returns_none_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = subprocess.CompletedProcess(
        args=["uv", "tool", "list"], returncode=0, stdout="", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert _uv_tool_installed_version("cptr", timeout=5.0) is None


def test_uv_tool_list_returns_none_on_nonzero_rc(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = subprocess.CompletedProcess(
        args=["uv", "tool", "list"], returncode=1, stdout="", stderr="boom"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert _uv_tool_installed_version("cptr", timeout=5.0) is None


def test_uv_tool_list_returns_none_when_uv_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _uv_tool_installed_version("cptr", timeout=5.0) is None


# --- available_versions ----------------------------------------------------


def test_available_versions_returns_latest_from_pypi() -> None:
    inst = CptrInstall()
    with patch(
        "genesis_worker.services.cptr.install._http_get_json",
        return_value=_fake_pypi_payload(),
    ):
        versions = inst.available_versions()
    assert len(versions) == 1
    v = versions[0]
    assert v.version == "0.9.21"
    assert v.size_bytes == 4_560_844
    assert v.sha256 == "deadbeef" * 8
    assert v.url.endswith("cptr-0.9.21-py3-none-any.whl")


def test_available_versions_returns_empty_on_network_error() -> None:
    inst = CptrInstall()

    def _raise(url: str, *, timeout: float):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("no internet")

    with patch("genesis_worker.services.cptr.install._http_get_json", _raise):
        assert inst.available_versions() == []


def test_available_versions_handles_missing_wheel() -> None:
    """PyPI records without a wheel URL fall back to the project page."""
    payload = _fake_pypi_payload()
    payload["urls"] = []
    inst = CptrInstall()
    with patch(
        "genesis_worker.services.cptr.install._http_get_json", return_value=payload
    ):
        versions = inst.available_versions()
    assert len(versions) == 1
    assert versions[0].version == "0.9.21"
    assert versions[0].size_bytes is None
    assert "pypi.org/project/cptr" in versions[0].url


# --- binary_path / state ---------------------------------------------------


def test_binary_path_via_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/local/bin/{name}")
    inst = CptrInstall()
    assert inst.binary_path() == Path("/usr/local/bin/cptr")


def test_binary_path_none_when_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    inst = CptrInstall()
    assert inst.binary_path() is None
    assert inst.state() == InstallState.NOT_INSTALLED


def test_state_installed_when_binary_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    inst = CptrInstall()
    assert inst.state() == InstallState.INSTALLED


# --- installed_version (no local cache) -----------------------------------


def test_installed_version_queries_uv_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """No state file: each call asks uv. Drift is impossible by construction."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")
    calls: list[str] = []

    def _fake_run(*a, **kw):  # type: ignore[no-untyped-def]
        calls.append("run")
        return subprocess.CompletedProcess(
            args=a[0] if a else ["uv"],
            returncode=0,
            stdout="cptr v1.2.3\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    inst = CptrInstall()
    assert inst.installed_version() == "1.2.3"
    assert inst.installed_version() == "1.2.3"
    assert len(calls) == 2  # No caching; both calls hit uv.


# --- install session -------------------------------------------------------


def _wait_for_terminal(session) -> AcquireView:  # type: ignore[no-untyped-def]
    """Drain the install session and return the final step."""
    session.wait()
    return session.view()


def test_install_session_runs_uv_tool_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session runs ``uv tool install cptr==<v>`` and finishes complete."""
    calls: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")

    inst = CptrInstall()
    session = inst.install(version="0.9.21")
    assert isinstance(session, object)  # AcquireSession ABC; not type-checked here
    step = _wait_for_terminal(session)
    assert step.kind == "complete"
    assert calls and calls[0][:3] == ["uv", "tool", "install"]
    assert calls[0][3] == "cptr==0.9.21"


def test_install_session_default_uses_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    """No version → spec is ``cptr@latest`` so uv picks the current version."""
    captured: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")

    inst = CptrInstall()
    session = inst.install()
    _wait_for_terminal(session)
    assert captured[0][3] == "cptr@latest"


def test_install_session_failure_when_uv_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="network error"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")

    inst = CptrInstall()
    step = _wait_for_terminal(inst.install(version="0.9.21"))
    assert step.kind == "failed"
    assert "network error" in (step.error or "")


def test_install_session_failure_when_binary_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uv succeeds but the binary isn't on PATH — surface as failed."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr("shutil.which", lambda name: None)

    inst = CptrInstall()
    step = _wait_for_terminal(inst.install(version="0.9.21"))
    assert step.kind == "failed"
    assert "PATH" in (step.error or "")


def test_install_session_missing_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(args, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    inst = CptrInstall()
    step = _wait_for_terminal(inst.install(version="0.9.21"))
    assert step.kind == "failed"
    assert "uv" in (step.error or "")


def test_install_session_submit_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """``submit`` doesn't drive state — the session is fire-and-forget."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/cptr")

    inst = CptrInstall()
    session = inst.install(version="0.9.21")
    session.submit(AcquireChoice())
    step = _wait_for_terminal(session)
    assert step.kind == "complete"


# --- uninstall -------------------------------------------------------------


def test_uninstall_runs_uv_tool_uninstall(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def _fake_run(args, **kw):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    inst = CptrInstall()
    inst.uninstall()
    assert captured and captured[0] == ["uv", "tool", "uninstall", "cptr"]


def test_uninstall_swallows_missing_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(args, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    inst = CptrInstall()
    inst.uninstall()  # must not raise