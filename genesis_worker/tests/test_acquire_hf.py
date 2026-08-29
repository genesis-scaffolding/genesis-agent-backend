"""Tests for :class:`HfAcquireSession` — the HF acquire state machine.

No real network I/O. ``HfApi.list_repo_tree`` and ``hf_hub_download``
are both injectable so the tests pass canned responses / record calls.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from genesis_worker.contracts import (
    AcquireSession,
    AcquireStateKind,
)
from genesis_worker.sources.huggingface import (
    HfAcquireChoice,
    HfAcquireSession,
    HfAcquireState,
    HfAcquireView,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockRepoFile:
    """Stand-in for ``huggingface_hub.RepoFile`` exposing path + size."""

    def __init__(self, path: str, size: int = 1024) -> None:
        self.path = path
        self.size = size


def _make_api(files: list[tuple[str, int]]) -> MagicMock:
    """Build a MagicMock HfApi whose list_repo_tree returns ``files``.

    Each tuple is (path, size). The mock auto-classifies by the GGUF
    filter in the source code; non-GGUF entries are dropped.
    """
    api = MagicMock()
    api.list_repo_tree.return_value = [_MockRepoFile(p, s) for p, s in files]
    return api


def _wait_for_terminal(
    session: HfAcquireSession, *, timeout: float = 2.0
) -> None:
    """Block until the session reaches COMPLETE/FAILED/CANCELLED.

    The download runs in a background thread; tests that need the
    terminal state have to wait for it. We poll ``view()`` until the
    kind changes or the deadline elapses.
    """
    deadline = time.monotonic() + timeout
    last_kind: AcquireStateKind | None = None
    while time.monotonic() < deadline:
        view = session.view()
        last_kind = view.kind
        if last_kind in (
            AcquireStateKind.COMPLETE,
            AcquireStateKind.FAILED,
            AcquireStateKind.CANCELLED,
        ):
            return
        time.sleep(0.01)
    raise AssertionError(f"session did not terminate in {timeout}s; kind={last_kind}")


# ---------------------------------------------------------------------------
# Inspection -> SELECTING
# ---------------------------------------------------------------------------


def test_first_view_runs_inspection(tmp_path: Path) -> None:
    """The first ``view()`` triggers inspection and returns SELECTING."""
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("model-Q8_0.gguf", 7_000_000_000),
        ("mmproj-Q8.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)

    view = session.view()
    assert isinstance(view, HfAcquireView)
    assert view.kind == AcquireStateKind.SELECTING
    targets = view.targets
    roles = [g.role for g in targets]
    assert roles.count("main") == 2
    assert roles.count("mmproj") == 1


def test_mmproj_only_passes_inspection_but_rejected_on_submit(tmp_path: Path) -> None:
    """An mmproj-only repo passes inspection (files are visible) but cannot be
    submitted as the main model."""
    api = _make_api([("mmproj-Q8.gguf", 1_000_000_000)])
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/vision-only")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)

    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    assert view.targets is not None
    assert len(view.targets) == 1
    assert view.targets[0].role == "mmproj"

    # Trying to submit mmproj as main is rejected.
    session.submit(HfAcquireChoice(main_indexes=[1]))
    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    assert view.error is not None
    assert "roles" in view.error and "mmproj" in view.error


def test_safetensor_repo_passes_inspection_and_submit(tmp_path: Path) -> None:
    """A safetensor-only repo surfaces in SELECTING and can be submitted."""
    api = _make_api(
        [("diffusion_model.safetensors", 5_000_000_000),
         ("text_encoder.safetensors", 3_000_000_000)]
    )
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/comfy-model")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)

    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    targets = view.targets
    assert len(targets) == 2
    for g in targets:
        assert g.role == "safetensor"

    # Multi-select: both safetensors as main_indexes.
    session.submit(HfAcquireChoice(main_indexes=[1, 2]))
    view = session.view()
    assert view.kind == AcquireStateKind.CONFIRMING


def test_safetensor_multi_select_downloads_all(tmp_path: Path) -> None:
    """Multi-select safetensors are all downloaded."""
    api = _make_api(
        [("a.safetensors", 1_000_000_000), ("b.safetensors", 2_000_000_000)]
    )
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/multi")
    recorded: list[dict[str, Any]] = []

    def fake_download(**kwargs: Any) -> str:
        recorded.append(kwargs)
        return "/tmp/fake"

    session = HfAcquireSession(
        api=api, hf_state=state, cache_dir=tmp_path, hf_hub_download=fake_download,
    )
    session.view()
    session.submit(HfAcquireChoice(main_indexes=[1, 2]))
    session.submit(HfAcquireChoice(confirm=True))
    _wait_for_terminal(session)
    paths = [c["filename"] for c in recorded]
    assert "a.safetensors" in paths
    assert "b.safetensors" in paths


def test_inspection_api_error_returns_failed(tmp_path: Path) -> None:
    """API exceptions during inspection surface as ``FAILED``."""
    api = MagicMock()
    api.list_repo_tree.side_effect = RuntimeError("network down")
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)

    view = session.view()
    assert view.kind == AcquireStateKind.FAILED
    assert view.error is not None
    assert "inspect failed" in view.error


def test_non_gguf_files_are_filtered(tmp_path: Path) -> None:
    """Only files ending in ``.gguf`` are inspected."""
    files = [
        ("README.md", 100),
        ("config.json", 200),
        ("model-Q4_K_M.gguf", 4_000_000_000),
    ]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)

    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    assert len(view.targets) == 1
    assert view.targets[0].role == "main"


# ---------------------------------------------------------------------------
# Shard grouping
# ---------------------------------------------------------------------------


def test_sharded_gguf_groups_into_one_group(tmp_path: Path) -> None:
    """A sharded model becomes one selectable group with multiple paths."""
    files = [
        ("model-00001-of-00003.gguf", 4_000_000_000),
        ("model-00002-of-00003.gguf", 4_000_000_000),
        ("model-00003-of-00003.gguf", 4_000_000_000),
    ]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/sharded")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)

    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    assert len(view.targets) == 1
    g = view.targets[0]
    assert g.is_sharded
    assert len(g.paths) == 3
    assert g.size == 12_000_000_000


# ---------------------------------------------------------------------------
# SELECTING -> CONFIRMING -> FETCHING -> COMPLETE
# ---------------------------------------------------------------------------


def test_full_happy_path_records_hf_hub_download_calls(tmp_path: Path) -> None:
    """Drive the state machine end-to-end; assert the right downloads happened.

    The download stub yields via a short sleep so the assertion on the
    intermediate ``FETCHING`` state isn't racy; ``_wait_for_terminal``
    then blocks until the thread finishes.
    """
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("model-Q8_0.gguf", 7_000_000_000),
        ("mmproj-Q8.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)

    # Step 1: inspection -> SELECTING.
    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    # File groups order: main (2), mmproj (1). Indexes 1, 2 = mains; 3 = mmproj.
    main_idx = 2  # the Q8_0 main (Q4 is index 1)
    aux_idx = 3  # mmproj

    # Step 2: submit main + aux -> CONFIRMING.
    session.submit(HfAcquireChoice(main_indexes=[main_idx], aux_indexes=[aux_idx]))
    view = session.view()
    assert view.kind == AcquireStateKind.CONFIRMING
    assert view.total_bytes == 7_000_000_000 + 1_000_000_000
    assert view.cache_dir == tmp_path

    # Step 3: confirm -> FETCHING. The download thread will run.
    recorded: list[dict[str, Any]] = []

    def fake_download(**kwargs: Any) -> str:
        recorded.append(kwargs)
        # Slow the stub down so the test can observe the FETCHING state.
        time.sleep(0.05)
        return "/tmp/fake-blob"

    session = HfAcquireSession(
        api=api, hf_state=state,
        cache_dir=tmp_path, hf_hub_download=fake_download,
    )
    session.submit(HfAcquireChoice(confirm=True))
    view = session.view()
    assert view.kind == AcquireStateKind.FETCHING
    assert view.cache_dir == tmp_path

    _wait_for_terminal(session)

    final = session.view()
    assert final.kind == AcquireStateKind.COMPLETE
    assert final.progress is not None
    assert final.progress.bytes_total == final.progress.bytes_done

    # The recorded calls should include both files with the right kwargs.
    paths = [c["filename"] for c in recorded]
    assert "model-Q8_0.gguf" in paths
    assert "mmproj-Q8.gguf" in paths
    # Q4_K_M.gguf was NOT selected.
    assert "model-Q4_K_M.gguf" not in paths
    # All calls used our cache_dir.
    assert all(c["cache_dir"] == str(tmp_path) for c in recorded)
    assert all(c["revision"] == "main" for c in recorded)
    assert all(c["repo_id"] == "acme/demo" for c in recorded)


def test_confirm_false_returns_to_select_files(tmp_path: Path) -> None:
    """Declining the confirm step sends the user back to file selection."""
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
    ]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)
    session.view()  # trigger inspecting

    session.submit(HfAcquireChoice(main_indexes=[1]))
    assert session.view().kind == AcquireStateKind.CONFIRMING

    session.submit(HfAcquireChoice(confirm=False))
    assert session.view().kind == AcquireStateKind.SELECTING


def test_invalid_main_indexes_returns_error_in_select_files(tmp_path: Path) -> None:
    files = [("model-Q4_K_M.gguf", 4_000_000_000)]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)
    session.view()

    session.submit(HfAcquireChoice(main_indexes=[99]))
    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    assert view.error is not None
    assert "out of range" in view.error


def test_selecting_non_main_as_main_returns_error(tmp_path: Path) -> None:
    """Selecting an mmproj as the main file is rejected."""
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("mmproj-Q8.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)
    session.view()
    # Index 2 is the mmproj.
    session.submit(HfAcquireChoice(main_indexes=[2]))
    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    assert view.error is not None
    assert "roles" in view.error


def test_selecting_two_mmprojs_returns_error(tmp_path: Path) -> None:
    """At most one mmproj is allowed."""
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("mmproj-Q8-A.gguf", 1_000_000_000),
        ("mmproj-Q8-B.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)
    session.view()
    # Indexes 1 (main), 2 and 3 (mmprojs).
    session.submit(HfAcquireChoice(main_indexes=[1], aux_indexes=[2, 3]))
    view = session.view()
    assert view.kind == AcquireStateKind.SELECTING
    assert view.error is not None
    assert "one mmproj" in view.error


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_before_download_transitions_to_cancelled(tmp_path: Path) -> None:
    """Cancelling from CONFIRMING lands on CANCELLED immediately."""
    files = [("model-Q4_K_M.gguf", 4_000_000_000)]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)
    session.view()
    session.submit(HfAcquireChoice(main_indexes=[1]))

    session.cancel()
    view = session.view()
    assert view.kind == AcquireStateKind.CANCELLED
    assert view.can_cancel is False


def test_cancel_mid_download_aborts_thread(tmp_path: Path) -> None:
    """The download thread sees the cancel event and stops cleanly."""
    files = [("model-Q4_K_M.gguf", 4_000_000_000)]
    api = _make_api(files)
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)
    session.view()
    session.submit(HfAcquireChoice(main_indexes=[1]))

    download_started = threading.Event()
    download_can_return = threading.Event()
    recorded: list[str] = []

    def slow_download(**kwargs: Any) -> str:
        recorded.append(kwargs["filename"])
        download_started.set()
        # Block until the test sets the event (or 2s elapses).
        download_can_return.wait(timeout=2.0)
        return "/tmp/fake"

    session = HfAcquireSession(
        api=api, hf_state=state,
        cache_dir=tmp_path, hf_hub_download=slow_download,
    )
    session.submit(HfAcquireChoice(confirm=True))
    download_started.wait(timeout=2.0)

    session.cancel()
    download_can_return.set()
    _wait_for_terminal(session)
    assert session.view().kind == AcquireStateKind.CANCELLED


# ---------------------------------------------------------------------------
# Log tail: worker writes and view reads from the same list
# ---------------------------------------------------------------------------


def test_progress_lines_visible_in_view(tmp_path: Path) -> None:
    """The worker appends progress lines to ``_log_tail``; ``view()`` must surface them.

    Regression: an earlier refactor had the worker writing to one list
    (``BackgroundSession._log_tail``) and the view reading from another
    (``HfAcquireState.log_tail``), so the UI's ``st.code(log_tail)``
    showed nothing.
    """
    api = _make_api([("model.gguf", 1_000_000_000)])

    def fake_download(**kwargs: Any) -> str:
        # Emit something on stderr so capture path is exercised too.
        print(f"downloading {kwargs['filename']}", file=sys.stderr)
        return "/tmp/fake"

    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path, hf_hub_download=fake_download)
    session.view()  # inspect
    session.submit(HfAcquireChoice(main_indexes=[1]))
    session.submit(HfAcquireChoice(confirm=True))
    _wait_for_terminal(session)

    view = session.view()
    assert view.kind == AcquireStateKind.COMPLETE
    assert view.log_tail is not None
    joined = "\n".join(view.log_tail)
    assert "Progress:" in joined
    assert "[stderr] downloading model.gguf" in joined


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_session_satisfies_acquire_session_protocol(tmp_path: Path) -> None:
    api = _make_api([])
    state = HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="acme/demo")
    session = HfAcquireSession(api=api, hf_state=state, cache_dir=tmp_path)
    assert isinstance(session, AcquireSession)
