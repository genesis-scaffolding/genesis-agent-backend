"""HuggingFace acquire session — state-machine-driven download wizard."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from fnmatch import fnmatch
from io import StringIO
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

from ...contracts import (
    AcquireChoice,
    AcquireProgress,
    AcquireState,
    AcquireStateKind,
    AcquireView,
)
from ...utils.background_session import BackgroundSession, _Canceled

GGUF_EXT = ".gguf"
RAD_EXT = ".rad"

# Mirror bin/hf-model.py: sharded GGUF naming pattern.
SHARD_RE = re.compile(
    r"^(?P<base>.+)-(?P<number>\d{5})-of-(?P<count>\d{5})(?P<ext>\.gguf)$",
    re.IGNORECASE,
)

# Role classification: same markers bin/hf-model.py used.
AUX_MARKERS = (
    "mmproj",
    "adapter",
    "lora",
    "controlnet",
    "text_encoder",
    "image_encoder",
    "vision_encoder",
    "vae",
    "draft",
)

_ROLE_ORDER = {
    "main": 0,
    "mmproj": 1,
    "mtp": 2,
    "safetensor": 0,
    "radiance": 0,
    "speech": 0,
    "unsupported": 3,
}

# The model card is the metadata a speech model is listed by: speaches
# skips a cache repo without it (ADR-048).
MODEL_CARD_FILENAME = "README.md"


@dataclass(frozen=True)
class SpeechLayout:
    """One file layout of a speech model a TTS/STT server can serve.

    ``required`` patterns must ALL be present for the layout to match;
    ``optional`` patterns are fetched when present. The names mirror
    speaches' own ``download_model_files`` allow-patterns (upstream
    ``src/speaches/executors/{kokoro,piper,whisper,parakeet}.py``).
    """

    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


SPEECH_LAYOUTS: tuple[SpeechLayout, ...] = (
    SpeechLayout("kokoro", ("model.onnx", "voices.bin")),
    SpeechLayout("piper", ("model.onnx", "config.json")),
    SpeechLayout(
        "ctranslate2",
        ("model.bin", "config.json", "tokenizer.json", "vocabulary.*"),
        ("preprocessor_config.json",),
    ),
    SpeechLayout(
        "parakeet",
        ("encoder-model*.onnx", "decoder_joint-model*.onnx", "vocab.txt"),
        ("config.json",),
    ),
)


def bundle_label(layout_name: str, count: int, directory: str) -> str:
    """Label of one speech bundle: its layout, its file count, its directory.

    The directory is part of the label because the label is the key the
    acquire UI resolves a selection back to, and two directories can hold
    the same layout. The repo root carries no directory suffix.
    """
    files = "file" if count == 1 else "files"
    where = "" if directory == "." else f" - {directory}"
    return f"speaches: {layout_name} ({count} {files}){where}"


@dataclass(frozen=True)
class _RemoteFile:
    """Internal mirror of ``huggingface_hub.RepoFile``.

    The library's RepoFile takes ``oid`` as a required kwarg in
    ``__init__`` (its constructor is not part of the public surface).
    We use this lighter dataclass instead so the module is testable
    without constructing library internals.
    """

    path: str
    size: int | None


@dataclass(frozen=True)
class AcquireFileGroup:
    """HF-local: one selectable file, a group of shards, or a speech bundle."""

    paths: list[str]
    size: int | None  # total across the group; None if any shard's size is unknown
    role: str  # "main", "mmproj", "mtp", "unsupported", "safetensor", "radiance", "speech"
    label: str
    is_sharded: bool


@dataclass
class HfAcquireState(AcquireState):
    """HF-specific state, extends the base with file-group selection."""

    kind: AcquireStateKind
    repo_id: str
    confirmed: bool = False
    bytes_done: int = 0
    bytes_total: int = 0
    log_tail: list[str] = field(default_factory=list)
    failure: str | None = None
    selected_main: list[AcquireFileGroup] = field(default_factory=list)
    selected_aux: list[AcquireFileGroup] = field(default_factory=list)


@dataclass(frozen=True)
class HfAcquireView(AcquireView):
    """HF-specific view, extends the base with selection targets."""

    targets: list[AcquireFileGroup] = field(default_factory=list)


@dataclass(frozen=True)
class HfAcquireChoice(AcquireChoice):
    """HF-specific choice, extends the base with file-group indexes."""

    main_indexes: list[int] | None = None
    aux_indexes: list[int] | None = None


def classify_path(path: str) -> str:
    """Classify a remote file path for the file-selection UI."""
    name = Path(path).name.lower()
    if ".gguf" in name:
        if "mmproj" in name:
            return "mmproj"
        if name.startswith("mtp-"):
            return "mtp"
        if any(marker in name for marker in AUX_MARKERS):
            return "unsupported"
        return "main"
    if path.lower().endswith(".safetensors"):
        return "safetensor"
    if path.lower().endswith(RAD_EXT):
        return "radiance"
    return "unsupported"


def group_files(files: list[_RemoteFile]) -> list[AcquireFileGroup]:
    """Group split GGUF files by their base filename.

    Returns ``AcquireFileGroup`` objects in role-then-label order.
    Mirrors ``bin/hf-model.py:group_files``.
    """
    grouped: dict[str, list[_RemoteFile]] = {}
    for item in files:
        match = SHARD_RE.match(item.path)
        key = match.group("base") + match.group("ext") if match else item.path
        grouped.setdefault(key, []).append(item)

    groups: list[AcquireFileGroup] = []
    for key, members in grouped.items():
        members.sort(key=lambda item: item.path.lower())
        role = classify_path(members[0].path)
        size = (
            sum(item.size for item in members if item.size is not None)
            if all(item.size is not None for item in members)
            else None
        )
        if len(members) == 1:
            label = members[0].path
        else:
            label = f"{key} ({len(members)} shards)"
        groups.append(
            AcquireFileGroup(
                paths=[item.path for item in members],
                size=size,
                role=role,
                label=label,
                is_sharded=len(members) > 1,
            )
        )

    return sorted(groups, key=lambda g: (_ROLE_ORDER.get(g.role, 9), g.label.lower()))


def speech_groups(files: list[_RemoteFile]) -> list[AcquireFileGroup]:
    """One group per complete speech-model layout found in the repo.

    A speech model is only loadable when its files land in the cache
    together (Kokoro needs ``voices.bin`` next to ``model.onnx``), so the
    selectable unit is the bundle, not the file. Files are grouped by
    directory first: a bundle's files must live in the same directory.
    The model card is part of the bundle — a speech server cannot list a
    repo without it — so a directory without a card yields no bundle.

    The label carries the directory: it is the key the acquire UI resolves
    a selection back to, and two directories can hold the same layout.
    """
    by_dir: dict[str, list[_RemoteFile]] = {}
    for item in files:
        by_dir.setdefault(str(Path(item.path).parent), []).append(item)

    groups: list[AcquireFileGroup] = []
    for directory in sorted(by_dir):
        members = by_dir[directory]
        card = sorted(f.path for f in members if Path(f.path).name == MODEL_CARD_FILENAME)
        if not card:
            continue
        for layout in SPEECH_LAYOUTS:
            weights: list[str] = []
            for pattern in layout.required:
                hits = sorted(f.path for f in members if fnmatch(Path(f.path).name, pattern))
                if not hits:
                    weights = []
                    break
                weights.extend(hits)
            if not weights:
                continue
            for pattern in layout.optional:
                weights.extend(
                    sorted(f.path for f in members if fnmatch(Path(f.path).name, pattern))
                )
            paths = [*weights, *card]
            weight_sizes = [f.size for f in members if f.path in weights]
            size = (
                sum(s for s in weight_sizes if s is not None)
                if all(s is not None for s in weight_sizes)
                else None
            )
            groups.append(
                AcquireFileGroup(
                    paths=paths,
                    size=size,
                    role="speech",
                    label=bundle_label(layout.name, len(weights), directory),
                    is_sharded=False,
                )
            )
            break

    return sorted(groups, key=lambda g: (_ROLE_ORDER.get(g.role, 9), g.label.lower()))


@contextmanager
def _capture_stderr():
    """Capture stderr for the duration of the block. HF-specific."""
    buf = StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = old


class HfAcquireSession(BackgroundSession):
    """State-machine acquire session for one HuggingFace repo.

    Construction:
        api       = HfApi() (or a mock in tests)
        hf_state  = HfAcquireState(repo_id='org/name')
        cache_dir = Path to the HF cache root
        revision  = branch / commit to inspect (default 'main')
        hf_hub_download = per-file download callable (default
                          ``huggingface_hub.hf_hub_download``; tests
                          inject a stub).
    """

    source_name = "huggingface"

    def __init__(
        self,
        *,
        api: HfApi,
        hf_state: HfAcquireState,
        cache_dir: Path,
        revision: str = "main",
        hf_hub_download: Callable[..., str] | None = None,
    ) -> None:
        super().__init__(state=hf_state)
        self._api = api
        self._cache_dir = cache_dir
        self._revision = revision
        self._hf_hub_download = hf_hub_download
        # Inspection results, populated by _inspect().
        self._groups: list[AcquireFileGroup] = []
        self._files: list[_RemoteFile] = []
        # Size of every inspected file, keyed by repo path — the progress
        # counter needs the per-file size, the group carries only the sum.
        self._file_sizes: dict[str, int | None] = {}
        # Transient selection error; surfaced via view() while in SELECTING.
        self._last_select_error: str | None = None
        # NOTE: inspection is lazy — runs synchronously on the first
        # view() that observes INSPECTING. Tests that construct the
        # session without inspecting don't pay the network round-trip.

    # --- Properties --------------------------------------------------------

    @property
    def repo_id(self) -> str:
        return self._state.repo_id

    # --- BackgroundSession protocol --------------------------------------

    def view(self) -> HfAcquireView:
        """Project the current state into a UI view."""
        if self._state.kind == AcquireStateKind.INSPECTING:
            self._inspect()
        kind = self._state.kind
        repo_id = self._state.repo_id

        if kind == AcquireStateKind.SELECTING:
            return HfAcquireView(
                kind=kind,
                title=f"Select files for {repo_id}",
                prompt="Pick file(s) and any auxiliaries",
                targets=self._groups,
                error=self._last_select_error,
                can_cancel=True,
            )
        if kind == AcquireStateKind.CONFIRMING:
            total_bytes = self._compute_total_bytes()
            n_groups = len(self._state.selected_main) + len(self._state.selected_aux)  # type: ignore[attr-defined]
            return HfAcquireView(
                kind=kind,
                title=f"Confirm download for {repo_id}",
                prompt=f"Will download {n_groups} group(s) ({total_bytes or '?'} bytes)",
                total_bytes=total_bytes,
                cache_dir=self._cache_dir,
                can_cancel=True,
            )
        if kind == AcquireStateKind.FETCHING:
            tail = self._state.log_tail[-20:]
            return HfAcquireView(
                kind=kind,
                title=f"Downloading {repo_id}",
                progress=AcquireProgress(
                    bytes_done=self._state.bytes_done,
                    bytes_total=self._state.bytes_total,
                    speed_bps=0,
                    eta_s=0,
                ),
                cache_dir=self._cache_dir,
                log_tail=tail,
                can_cancel=True,
            )
        if kind == AcquireStateKind.COMPLETE:
            tail = self._state.log_tail[-20:]
            return HfAcquireView(
                kind=kind,
                title=f"Downloaded {repo_id}",
                progress=AcquireProgress(
                    bytes_done=self._state.bytes_total,
                    bytes_total=self._state.bytes_total,
                    speed_bps=0,
                    eta_s=0,
                ),
                log_tail=tail,
                can_cancel=False,
            )
        if kind == AcquireStateKind.FAILED:
            tail = self._state.log_tail[-20:]
            return HfAcquireView(
                kind=kind,
                title=f"Failed: {repo_id}",
                error=self._state.failure,
                log_tail=tail,
                can_cancel=False,
            )
        if kind == AcquireStateKind.CANCELLED:
            return HfAcquireView(
                kind=kind,
                title="Cancelled",
                can_cancel=False,
            )
        # INSPECTING — transient; inspection runs synchronously in __init__,
        # so the next call should be SELECTING or FAILED. This branch exists
        # only if the caller reads view() before inspection finishes.
        return HfAcquireView(
            kind=kind,
            title=f"Inspecting {repo_id}",
            can_cancel=True,
        )

    def submit(self, choice: AcquireChoice) -> None:
        kind = self._state.kind
        if kind == AcquireStateKind.SELECTING:
            self._submit_select_files(choice)
        elif kind == AcquireStateKind.CONFIRMING:
            self._submit_confirm_storage(choice)
        # FETCHING / terminal: no-op (submit doesn't transition those).

    # --- Inspection --------------------------------------------------------

    def _inspect(self) -> None:
        """Walk the repo tree; build file groups; transition to SELECTING or FAILED."""
        try:
            tree = self._api.list_repo_tree(
                self._state.repo_id,
                repo_type="model",
                revision=self._revision,
                recursive=True,
            )
        except Exception as exc:  # noqa: BLE001 — HF raises many error types
            self._state.kind = AcquireStateKind.FAILED
            self._state.failure = f"inspect failed: {exc}"
            return

        all_files: list[_RemoteFile] = []
        for item in tree:
            path = getattr(item, "path", None)
            if not path:
                continue
            if not hasattr(item, "size"):
                continue
            size = getattr(item, "size", None)
            all_files.append(_RemoteFile(path=path, size=size))

        gguf_files = [f for f in all_files if f.path.lower().endswith(GGUF_EXT)]
        st_files = [f for f in all_files if f.path.lower().endswith(".safetensors")]
        rad_files = [f for f in all_files if f.path.lower().endswith(RAD_EXT)]

        self._files = sorted(gguf_files, key=lambda f: f.path.lower())
        self._file_sizes = {f.path: f.size for f in all_files}
        self._groups = group_files(self._files)
        # Each safetensor file is its own unsplittable group.
        for f in st_files:
            self._groups.append(
                AcquireFileGroup(
                    paths=[f.path],
                    size=f.size,
                    role="safetensor",
                    label=f.path,
                    is_sharded=False,
                )
            )
        # Each .rad file is its own unsplittable group.
        for f in rad_files:
            self._groups.append(
                AcquireFileGroup(
                    paths=[f.path],
                    size=f.size,
                    role="radiance",
                    label=f.path,
                    is_sharded=False,
                )
            )
        # A speech model is one bundle: its files plus its model card.
        self._groups += speech_groups(all_files)
        self._groups.sort(key=lambda g: (_ROLE_ORDER.get(g.role, 9), g.label.lower()))

        if not self._groups:
            self._state.kind = AcquireStateKind.FAILED
            self._state.failure = (
                "repository has no .gguf, .safetensors, .rad, or complete speech-model "
                "files (a speech model needs its .onnx / .bin files together plus README.md)"
            )
            return

        self._state.kind = AcquireStateKind.SELECTING

    # --- SELECTING -> CONFIRMING -----------------------------------------

    def _submit_select_files(self, choice: AcquireChoice) -> None:
        if not isinstance(choice, HfAcquireChoice):
            return
        if not choice.main_indexes:
            self._last_select_error = "select at least one file"
            return

        selected_groups: list[AcquireFileGroup] = []
        for idx in choice.main_indexes:
            if idx < 1 or idx > len(self._groups):
                self._last_select_error = f"index {idx} out of range"
                return
            selected_groups.append(self._groups[idx - 1])

        main_roles = {g.role for g in selected_groups}
        roles_seen = "/".join(sorted(main_roles)) or "(none)"
        if "radiance" in main_roles and len(main_roles) > 1:
            self._last_select_error = (
                f"selected roles [{roles_seen}]; radiance must be selected alone"
            )
            return
        if "speech" in main_roles and (len(main_roles) > 1 or len(selected_groups) > 1):
            self._last_select_error = (
                f"selected roles [{roles_seen}]; a speech model must be selected alone"
            )
            return
        if not (
            "safetensor" in main_roles
            or (main_roles == {"main"} and len(selected_groups) == 1)
            or (main_roles == {"radiance"} and len(selected_groups) == 1)
            or (main_roles == {"speech"} and len(selected_groups) == 1)
        ):
            self._last_select_error = f"selected roles [{roles_seen}]; need exactly one 'main', 'safetensor', 'radiance', or 'speech' group"
            return

        aux: list[AcquireFileGroup] = []
        for idx in choice.aux_indexes or []:
            if idx < 1 or idx > len(self._groups):
                self._last_select_error = f"aux index {idx} out of range"
                return
            aux.append(self._groups[idx - 1])

        if "speech" in main_roles and aux:
            self._last_select_error = "a speech model takes no auxiliary files"
            return

        if "safetensor" not in main_roles:
            if sum(g.role == "mmproj" for g in aux) > 1:
                self._last_select_error = "select at most one mmproj"
                return
            if sum(g.role == "mtp" for g in aux) > 1:
                self._last_select_error = "select at most one MTP/draft"
                return

        self._state.selected_main = selected_groups  # type: ignore[attr-defined]
        self._state.selected_aux = aux  # type: ignore[attr-defined]
        self._last_select_error = None
        self._state.kind = AcquireStateKind.CONFIRMING

    # --- CONFIRMING -> FETCHING ------------------------------------------

    def _submit_confirm_storage(self, choice: AcquireChoice) -> None:
        if not choice.confirm:
            # User declined: back to SELECTING, clear selection.
            self._state.selected_main = []  # type: ignore[attr-defined]
            self._state.selected_aux = []  # type: ignore[attr-defined]
            self._last_select_error = None
            self._state.kind = AcquireStateKind.SELECTING
            return
        self._state.confirmed = True
        self._state.bytes_total = self._compute_total_bytes() or 0
        self._state.kind = AcquireStateKind.FETCHING
        self._start()

    def _compute_total_bytes(self) -> int | None:
        sizes = [
            g.size
            for g in (*self._state.selected_main, *self._state.selected_aux)  # type: ignore[attr-defined]
        ]
        if all(s is not None for s in sizes):
            return sum(s for s in sizes if s is not None)
        return None

    # --- Worker thread: download ------------------------------------------

    def _run_inner(self) -> None:
        download = self._hf_hub_download or hf_hub_download
        selected = [*self._state.selected_main, *self._state.selected_aux]  # type: ignore[attr-defined]
        if not selected:
            raise RuntimeError("internal: no selected_main when downloading")
        total_files = sum(len(g.paths) for g in selected)
        done = 0
        files_done = 0
        total = sum(s.size for s in selected if s.size is not None)
        for group in selected:
            if self._cancel_event.is_set():
                raise _Canceled
            group_done = 0
            for path in group.paths:
                if self._cancel_event.is_set():
                    raise _Canceled
                try:
                    with _capture_stderr() as buf:
                        download(
                            repo_id=self._state.repo_id,
                            filename=path,
                            cache_dir=str(self._cache_dir),
                            revision=self._revision,
                        )
                    for line in buf.getvalue().rstrip().splitlines():
                        if line.strip():
                            self._append_log(f"[stderr] {line}")
                except _Canceled:
                    raise
                except Exception as exc:
                    # Preserve detail; supervisor captures the failure.
                    raise RuntimeError(f"download failed for {path}: {exc}") from exc
                if self._cancel_event.is_set():
                    raise _Canceled
                files_done += 1
                group_done += self._file_bytes(path)
                self._state.bytes_done = done + group_done
                self._state.bytes_total = total
                self._append_log(
                    f"Progress: {done + group_done}/{total} bytes ({files_done}/{total_files} files)"
                )
            done += group.size or 0
            self._state.bytes_done = done
            self._state.bytes_total = total
        self._fetch_model_card(download, selected)
        self._state.kind = AcquireStateKind.COMPLETE

    def _file_bytes(self, path: str) -> int:
        """Bytes one downloaded file adds to the progress counter.

        The model card is excluded: it is metadata, not weights, and it is
        not part of the group's size — counting it would overshoot
        ``bytes_total``.
        """
        if Path(path).name == MODEL_CARD_FILENAME:
            return 0
        return self._file_sizes.get(path) or 0

    def _fetch_model_card(
        self,
        download: Callable[..., str],
        selected: list[AcquireFileGroup],
    ) -> None:
        """Fetch the model card for an acquire that did not carry one.

        The card is the metadata a speech server lists a model by, so it is
        worth having for every repo. The fetch is best-effort here: the
        weights are already on disk, so a missing card is a warning in the
        log, not a failed acquire. A speech bundle carries its card in its
        own paths, and a failure there fails the acquire loudly.
        """
        if any(Path(p).name == MODEL_CARD_FILENAME for g in selected for p in g.paths):
            return
        try:
            with _capture_stderr():
                download(
                    repo_id=self._state.repo_id,
                    filename=MODEL_CARD_FILENAME,
                    cache_dir=str(self._cache_dir),
                    revision=self._revision,
                )
            self._append_log(f"Fetched model card ({MODEL_CARD_FILENAME})")
        except _Canceled:
            raise
        except Exception as exc:  # noqa: BLE001 — the weights are already on disk
            self._append_log(f"Model card not fetched: {exc}")


__all__ = [
    "MODEL_CARD_FILENAME",
    "SPEECH_LAYOUTS",
    "AcquireFileGroup",
    "HfAcquireChoice",
    "HfAcquireSession",
    "HfAcquireState",
    "HfAcquireView",
    "SpeechLayout",
    "bundle_label",
    "classify_path",
    "group_files",
    "speech_groups",
]
