"""Tests for :class:`HfAcquireSession` — the HF acquire state machine.

No real network I/O. ``HfApi.list_repo_tree`` and ``hf_hub_download``
are both injectable so the tests pass canned responses / record calls.

Spec-002 verification step 5: ``drive the session through one cycle;
assert the right ``hf_hub_download`` calls were made with the right
``cache_dir``. No real network I/O.``
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from genesis_worker.contracts import (
    AcquireChoice,
    AcquireState,
)
from genesis_worker.sources.huggingface import HfAcquireSession

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
    """Block until the session reaches complete/failed/cancelled.

    The download runs in a background thread; tests that need the
    terminal state have to wait for it. We poll ``current_step()``
    until the kind changes or the deadline elapses.
    """
    deadline = time.monotonic() + timeout
    last_kind: str = ""
    while time.monotonic() < deadline:
        last_kind = session.current_step().kind
        if last_kind in {"complete", "failed", "cancelled"}:
            return
        time.sleep(0.01)
    raise AssertionError(f"session did not terminate in {timeout}s; kind={last_kind}")


# ---------------------------------------------------------------------------
# Inspection -> select_files
# ---------------------------------------------------------------------------


def test_first_current_step_runs_inspection(tmp_path: Path) -> None:
    """The first ``current_step()`` triggers inspection and returns ``select_files``."""
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("model-Q8_0.gguf", 7_000_000_000),
        ("mmproj-Q8.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)

    step = session.current_step()
    assert step.kind == "select_files"
    file_groups = step.file_groups
    assert file_groups is not None
    # main groups appear before mmproj by role order.
    roles = [g.role for g in file_groups]
    assert roles.count("main") == 2
    assert roles.count("mmproj") == 1


def test_no_main_files_returns_failed(tmp_path: Path) -> None:
    """A repo with only mmproj/MTP fails inspection."""
    api = _make_api([("mmproj-Q8.gguf", 1_000_000_000)])
    state = AcquireState("huggingface", "acme/vision-only")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)

    step = session.current_step()
    assert step.kind == "failed"
    assert "no main" in (step.error or "")


def test_inspection_api_error_returns_failed(tmp_path: Path) -> None:
    """API exceptions during inspection surface as ``failed``."""
    api = MagicMock()
    api.list_repo_tree.side_effect = RuntimeError("network down")
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)

    step = session.current_step()
    assert step.kind == "failed"
    assert "inspect failed" in (step.error or "")


def test_non_gguf_files_are_filtered(tmp_path: Path) -> None:
    """Only files ending in ``.gguf`` are inspected."""
    files = [
        ("README.md", 100),
        ("config.json", 200),
        ("model-Q4_K_M.gguf", 4_000_000_000),
    ]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)

    step = session.current_step()
    assert step.kind == "select_files"
    file_groups = step.file_groups
    assert file_groups is not None
    assert len(file_groups) == 1
    assert file_groups[0].role == "main"


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
    state = AcquireState("huggingface", "acme/sharded")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)

    step = session.current_step()
    assert step.kind == "select_files"
    file_groups = step.file_groups
    assert file_groups is not None
    assert len(file_groups) == 1
    g = file_groups[0]
    assert g.is_sharded
    assert len(g.paths) == 3
    assert g.size == 12_000_000_000


# ---------------------------------------------------------------------------
# select_files -> confirm_storage -> downloading -> complete
# ---------------------------------------------------------------------------


def test_full_happy_path_records_hf_hub_download_calls(tmp_path: Path) -> None:
    """Drive the state machine end-to-end; assert the right downloads happened.

    This is the spec-002 verification step 5 gate. The download stub
    yields via a short sleep so the assertion on the intermediate
    ``downloading`` state isn't racy; ``_wait_for_terminal`` then
    blocks until the thread finishes.
    """
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("model-Q8_0.gguf", 7_000_000_000),
        ("mmproj-Q8.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)

    # Step 1: inspection -> select_files.
    step = session.current_step()
    assert step.kind == "select_files"
    # File groups order: main (2), mmproj (1). Indexes 1, 2 = mains; 3 = mmproj.
    main_idx = 2  # the Q8_0 main (Q4 is index 1)
    aux_idx = 3  # mmproj

    # Step 2: submit main + aux -> confirm_storage.
    next_step = session.submit(
        AcquireChoice(main_index=main_idx, aux_indexes=[aux_idx])
    )
    assert next_step.kind == "confirm_storage"
    assert next_step.total_bytes == 7_000_000_000 + 1_000_000_000
    assert next_step.cache_dir == tmp_path

    # Step 3: confirm -> downloading. The download thread will run.
    recorded: list[dict[str, Any]] = []

    def fake_download(**kwargs: Any) -> str:
        recorded.append(kwargs)
        # Slow the stub down so the test can observe the downloading state.
        time.sleep(0.05)
        return "/tmp/fake-blob"

    session = HfAcquireSession(
        api, state, cache_dir=tmp_path, hf_hub_download=fake_download,
    )
    next_step = session.submit(AcquireChoice(confirm=True))
    assert next_step.kind == "downloading"
    assert next_step.cache_dir == tmp_path

    _wait_for_terminal(session)

    final = session.current_step()
    assert final.kind == "complete"
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
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)
    session.current_step()  # trigger inspecting

    session.submit(AcquireChoice(main_index=1))
    assert session.current_step().kind == "confirm_storage"

    next_step = session.submit(AcquireChoice(confirm=False))
    assert next_step.kind == "select_files"


def test_invalid_main_index_returns_error_in_select_files(tmp_path: Path) -> None:
    files = [("model-Q4_K_M.gguf", 4_000_000_000)]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)
    session.current_step()

    next_step = session.submit(AcquireChoice(main_index=99))
    assert next_step.kind == "select_files"
    assert "out of range" in (next_step.error or "")


def test_selecting_non_main_as_main_returns_error(tmp_path: Path) -> None:
    """Selecting an mmproj as the main file is rejected."""
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("mmproj-Q8.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)
    session.current_step()
    # Index 2 is the mmproj.
    next_step = session.submit(AcquireChoice(main_index=2))
    assert next_step.kind == "select_files"
    assert "must be 'main'" in (next_step.error or "")


def test_selecting_two_mmprojs_returns_error(tmp_path: Path) -> None:
    """At most one mmproj is allowed."""
    files = [
        ("model-Q4_K_M.gguf", 4_000_000_000),
        ("mmproj-Q8-A.gguf", 1_000_000_000),
        ("mmproj-Q8-B.gguf", 1_000_000_000),
    ]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)
    session.current_step()
    # Indexes 1 (main), 2 and 3 (mmprojs).
    next_step = session.submit(AcquireChoice(main_index=1, aux_indexes=[2, 3]))
    assert next_step.kind == "select_files"
    assert "one mmproj" in (next_step.error or "")


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_before_download_transitions_to_cancelled(tmp_path: Path) -> None:
    """Cancelling from confirm_storage lands on ``cancelled`` immediately."""
    files = [("model-Q4_K_M.gguf", 4_000_000_000)]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)
    session.current_step()
    session.submit(AcquireChoice(main_index=1))

    session.cancel()
    step = session.current_step()
    assert step.kind == "cancelled"
    assert step.can_cancel is False


def test_cancel_mid_download_aborts_thread(tmp_path: Path) -> None:
    """The download thread sees the cancel event and stops cleanly."""
    files = [("model-Q4_K_M.gguf", 4_000_000_000)]
    api = _make_api(files)
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)
    session.current_step()
    session.submit(AcquireChoice(main_index=1))

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
        api, state, cache_dir=tmp_path, hf_hub_download=slow_download,
    )
    session.submit(AcquireChoice(confirm=True))
    download_started.wait(timeout=2.0)

    session.cancel()
    download_can_return.set()
    _wait_for_terminal(session)
    assert session.current_step().kind == "cancelled"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_session_satisfies_acquire_session_protocol(tmp_path: Path) -> None:
    from genesis_worker.contracts import AcquireSession

    api = _make_api([])
    state = AcquireState("huggingface", "acme/demo")
    session = HfAcquireSession(api, state, cache_dir=tmp_path)
    assert isinstance(session, AcquireSession)