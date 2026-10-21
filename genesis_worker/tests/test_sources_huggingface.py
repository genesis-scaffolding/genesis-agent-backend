"""Tests for the HuggingFace cache walker."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.contracts import AcquireSession
from genesis_worker.sources.huggingface import HfAcquireSession, HuggingFaceSource
from genesis_worker.sources.lmstudio import LMSource
from genesis_worker.tests._factories import source_ctx


@pytest.fixture
def fake_hub(tmp_path: Path) -> Path:
    """Build a minimal HF cache layout under tmp_path.

    Layout:
        <tmp>/huggingface/hub/
            models--acme--demo/
                refs/main                  (contains a sha)
                snapshots/<sha>/
                    model-Q4.gguf
                    mmproj-Q8.gguf
                    config.json
    """
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--demo"
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "model-Q4.gguf").write_bytes(b"\x00" * 1024)
    (snapshot / "mmproj-Q8.gguf").write_bytes(b"\x00" * 512)
    (snapshot / "config.json").write_text("{}")
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc123")
    return hub


def test_walk_returns_one_entry_per_repo(fake_hub: Path) -> None:
    src = HuggingFaceSource(source_ctx(local_path=fake_hub))
    models = src.walk()
    assert len(models) == 1
    m = models[0]
    assert m.source == "huggingface"
    assert m.native_id == "acme/demo"
    # total_bytes sums ALL files (weights + configs).
    assert m.total_bytes == 1024 + 512 + len("{}")
    assert m.extra["snapshot"] == "abc123"


def test_walk_classifies_pieces(fake_hub: Path) -> None:
    src = HuggingFaceSource(source_ctx(local_path=fake_hub))
    m = src.walk()[0]
    roles = {p.role for p in m.pieces}
    assert "main" in roles
    assert "mmproj" in roles
    assert "config" in roles


def test_walk_returns_empty_when_no_hub(tmp_path: Path) -> None:
    src = HuggingFaceSource(source_ctx(tmp_path, local_path=tmp_path / "nope"))
    assert src.walk() == []
    assert src.is_available() is False


def test_walk_skips_repo_without_snapshots(tmp_path: Path) -> None:
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--broken"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text("abc")
    # no snapshots dir
    src = HuggingFaceSource(source_ctx(local_path=hub))
    assert src.walk() == []


def test_walk_includes_repos_without_refs_main(tmp_path: Path) -> None:
    """refs/main is no longer required; snapshots are the source of truth."""
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--norefs"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "model.gguf").write_bytes(b"\x00")
    src = HuggingFaceSource(source_ctx(local_path=hub))
    models = src.walk()
    assert len(models) == 1
    assert models[0].native_id == "acme/norefs"


def test_walk_merges_all_snapshot_revisions(tmp_path: Path) -> None:
    """One entry per repo; pieces from every revision merged and deduped."""
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--multi"
    s1 = repo / "snapshots" / "aaa111"
    s1.mkdir(parents=True)
    (s1 / "model.gguf").write_bytes(b"\x00" * 100)
    (s1 / "shared.safetensors").write_bytes(b"\x00" * 50)
    s2 = repo / "snapshots" / "bbb222"
    s2.mkdir(parents=True)
    (s2 / "lora.safetensors").write_bytes(b"\x00" * 25)
    (s2 / "shared.safetensors").symlink_to("../aaa111/shared.safetensors")
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("bbb222")
    src = HuggingFaceSource(source_ctx(local_path=hub))
    models = src.walk()
    assert len(models) == 1
    m = models[0]
    assert m.native_id == "acme/multi"
    # 3 unique files: model + lora + shared (deduped across revisions)
    assert len(m.pieces) == 3
    assert m.total_bytes == 100 + 50 + 25
    assert m.extra["snapshots"] == ["aaa111", "bbb222"]
    assert m.extra["snapshot"] == "aaa111"


def test_walk_notes_when_no_weights(tmp_path: Path) -> None:
    hub = tmp_path / "huggingface" / "hub"
    repo = hub / "models--acme--config-only"
    snapshot = repo / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    (repo / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc")
    src = HuggingFaceSource(source_ctx(local_path=hub))
    m = src.walk()[0]
    assert "no model weights on disk" in m.notes


# ---------------------------------------------------------------------------
# Acquisition is reached through the source, not through plugin internals
# ---------------------------------------------------------------------------


def test_start_acquire_returns_a_session(tmp_path: Path) -> None:
    """The source is the unit of extensibility; callers never import .acquire."""
    src = HuggingFaceSource(source_ctx(local_path=tmp_path, options={"default_revision": "dev"}))
    session = src.start_acquire("acme/demo")
    assert isinstance(session, AcquireSession)
    assert session.source_name == "huggingface"
    assert session.repo_id == "acme/demo"


def test_start_acquire_uses_the_configured_revision(tmp_path: Path) -> None:
    src = HuggingFaceSource(source_ctx(local_path=tmp_path, options={"default_revision": "dev"}))
    session = src.start_acquire("acme/demo")
    assert isinstance(session, HfAcquireSession)
    assert session._revision == "dev"


def test_lmstudio_cannot_acquire(tmp_path: Path) -> None:
    """A source that doesn't acquire raises rather than pretending."""
    src = LMSource(source_ctx(local_path=tmp_path))
    assert src.can_acquire is False
    with pytest.raises(NotImplementedError):
        src.start_acquire("acme/demo")


# ---------------------------------------------------------------------------
# .rad acquisition support
# ---------------------------------------------------------------------------


def test_classify_path_radiance():
    from genesis_worker.sources.huggingface.acquire import classify_path

    assert classify_path("qwen3.8-27b-mxfp4.rad") == "radiance"
    assert classify_path("model.RAD") == "radiance"
    assert classify_path("path/to/file.rad") == "radiance"
    # Ensure gguf/safetensor still work
    assert classify_path("model.Q4_K_M.gguf") == "main"
    assert classify_path("weights.safetensors") == "safetensor"


def test_group_files_radiance_single():
    from genesis_worker.sources.huggingface.acquire import _RemoteFile, group_files

    files = [
        _RemoteFile(path="qwen3.8-27b-mxfp4.rad", size=19 * 1024**3),
    ]
    groups = group_files(files)
    assert len(groups) == 1
    assert groups[0].role == "radiance"
    assert groups[0].is_sharded is False
    assert groups[0].paths == ["qwen3.8-27b-mxfp4.rad"]


class _FakeTreeItem:
    def __init__(self, path: str, size: int) -> None:
        self.path = path
        self.size = size


class _FakeApi:
    def list_repo_tree(self, *args: object, **kwargs: object):
        return iter([])


def test_inspect_rad_only_repo_selects(tmp_path: Path) -> None:
    from genesis_worker.contracts import AcquireStateKind
    from genesis_worker.sources.huggingface.acquire import HfAcquireSession, HfAcquireState

    fake_api = _FakeApi()
    fake_api.list_repo_tree = lambda *a, **k: iter([_FakeTreeItem("qwen3.8-27b.rad", 19 * 1024**3)])

    session = HfAcquireSession(
        api=fake_api,  # type: ignore[arg-type]
        hf_state=HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="stilldeadcode/test"),
        cache_dir=tmp_path,
    )
    session.view()
    assert session._state.kind == AcquireStateKind.SELECTING
    assert len(session._groups) == 1
    assert session._groups[0].role == "radiance"


def test_inspect_mixed_rad_and_gguf(tmp_path: Path) -> None:
    from genesis_worker.contracts import AcquireStateKind
    from genesis_worker.sources.huggingface.acquire import HfAcquireSession, HfAcquireState

    fake_api = _FakeApi()
    fake_api.list_repo_tree = lambda *a, **k: iter(
        [
            _FakeTreeItem("model.Q4_K_M.gguf", 1024 * 1024 * 1024),
            _FakeTreeItem("qwen3.8-27b.rad", 19 * 1024**3),
        ]
    )

    session = HfAcquireSession(
        api=fake_api,  # type: ignore[arg-type]
        hf_state=HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="stilldeadcode/test"),
        cache_dir=tmp_path,
    )
    session.view()
    assert session._state.kind == AcquireStateKind.SELECTING
    roles = {g.role for g in session._groups}
    assert "main" in roles
    assert "radiance" in roles


def test_inspect_no_weights_fails(tmp_path: Path) -> None:
    from genesis_worker.contracts import AcquireStateKind
    from genesis_worker.sources.huggingface.acquire import HfAcquireSession, HfAcquireState

    session = HfAcquireSession(
        api=_FakeApi(),  # type: ignore[arg-type]
        hf_state=HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="stilldeadcode/empty"),
        cache_dir=tmp_path,
    )
    session.view()
    assert session._state.kind == AcquireStateKind.FAILED
    assert (
        session._state.failure is not None
        and "no .gguf, .safetensors, or .rad files" in session._state.failure
    )


def test_submit_select_radiance_succeeds(tmp_path: Path) -> None:
    from genesis_worker.contracts import AcquireStateKind
    from genesis_worker.sources.huggingface.acquire import (
        HfAcquireChoice,
        HfAcquireSession,
        HfAcquireState,
    )

    fake_api = _FakeApi()
    fake_api.list_repo_tree = lambda *a, **k: iter([_FakeTreeItem("qwen3.8-27b.rad", 19 * 1024**3)])

    session = HfAcquireSession(
        api=fake_api,  # type: ignore[arg-type]
        hf_state=HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="stilldeadcode/test"),
        cache_dir=tmp_path,
    )
    session.view()
    session.submit(HfAcquireChoice(main_indexes=[1]))
    assert session._state.kind == AcquireStateKind.CONFIRMING


def test_submit_select_radiance_plus_safetensor_fails(tmp_path: Path) -> None:
    from genesis_worker.contracts import AcquireStateKind
    from genesis_worker.sources.huggingface.acquire import (
        HfAcquireChoice,
        HfAcquireSession,
        HfAcquireState,
    )

    fake_api = _FakeApi()
    fake_api.list_repo_tree = lambda *a, **k: iter(
        [
            _FakeTreeItem("qwen3.8-27b.rad", 19 * 1024**3),
            _FakeTreeItem("weights.safetensors", 1024 * 1024 * 500),
        ]
    )

    session = HfAcquireSession(
        api=fake_api,  # type: ignore[arg-type]
        hf_state=HfAcquireState(kind=AcquireStateKind.INSPECTING, repo_id="stilldeadcode/test"),
        cache_dir=tmp_path,
    )
    session.view()
    session.submit(HfAcquireChoice(main_indexes=[1, 2]))
    assert session._state.kind == AcquireStateKind.SELECTING
    assert session._last_select_error is not None and "radiance" in session._last_select_error


def test_walk_classifies_rad_as_main(fake_hub: Path) -> None:
    """Catalog walk: .rad files in WEIGHT_EXTS get role 'main'."""
    from genesis_worker.sources.huggingface import HuggingFaceSource

    # Add a .rad file to the existing fake_hub snapshot
    rad_path = fake_hub / "models--acme--demo" / "snapshots" / "abc123" / "qwen3.8-27b.rad"
    rad_path.write_bytes(b"\x00" * 100)

    src = HuggingFaceSource(source_ctx(local_path=fake_hub))
    models = src.walk()
    assert len(models) == 1
    m = models[0]
    roles = {p.role for p in m.pieces}
    assert "main" in roles
    # The .rad piece has role 'main' via classify.py
    rad_pieces = [p for p in m.pieces if p.filename.endswith(".rad")]
    assert len(rad_pieces) == 1
    assert rad_pieces[0].role == "main"
