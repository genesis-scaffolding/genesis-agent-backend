"""Parser for ``docker pull --progress=json`` output.

Docker emits one JSON object per line on stderr. The objects describe
per-layer state transitions:

    {"status": "Pulling fs layer", "id": "abc"}
    {"status": "Waiting",          "id": "abc"}
    {"status": "Downloading",      "id": "abc", "progressDetail": {"current": N, "total": M}}
    {"status": "Verifying Checksum","id": "abc"}
    {"status": "Download complete", "id": "abc"}
    {"status": "Extracting",        "id": "abc", "progressDetail": {"current": N, "total": M}}
    {"status": "Pull complete",     "id": "abc"}
    {"status": "Digest: sha256:..."}
    {"status": "Status: Downloaded newer image for ..."}

This module tracks per-layer state and exposes a single ``snapshot()``
method that returns the bytes-downloaded, bytes-total, and a human-
readable phase string. Callers publish that as :class:`AcquireProgress`
plus a title; the UI's existing ``st.progress`` branch in
``ui/image.py:_render_step`` does the rest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class _Phase(StrEnum):
    PULLING = "pulling"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    EXTRACTING = "extracting"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


_PHASE_LABELS = {
    _Phase.PULLING: "Pulling fs layer",
    _Phase.DOWNLOADING: "Downloading",
    _Phase.VERIFYING: "Verifying checksum",
    _Phase.EXTRACTING: "Extracting",
    _Phase.COMPLETE: "Pull complete",
    _Phase.UNKNOWN: "Working",
}


@dataclass
class _LayerState:
    current: int = 0
    total: int = 0
    phase: _Phase = _Phase.PULLING


@dataclass(frozen=True)
class DockerPullSnapshot:
    bytes_done: int
    bytes_total: int
    phase: str  # human-readable label


class DockerPullProgress:
    """Streaming parser for ``docker pull --progress=json`` output.

    Call :meth:`update` once per line of docker's stderr. Call
    :meth:`snapshot` to read the aggregate (done, total, phase_text)
    suitable for publishing as ``AcquireProgress``.
    """

    def __init__(self) -> None:
        self._layers: dict[str, _LayerState] = {}
        self._aggregate_phase: _Phase = _Phase.UNKNOWN

    def update(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON line (e.g. plain-text fallback). Ignore.
            return
        if not isinstance(event, dict):
            return
        self._apply(event)

    def _apply(self, event: dict[str, Any]) -> None:
        status = str(event.get("status", ""))
        layer_id = event.get("id")
        detail = event.get("progressDetail") or {}
        current = int(detail.get("current", 0) or 0)
        total = int(detail.get("total", 0) or 0)

        if layer_id:
            layer = self._layers.setdefault(str(layer_id), _LayerState())
            if current:
                layer.current = current
            if total:
                layer.total = total
            layer.phase = self._phase_for_status(status)
            self._aggregate_phase = layer.phase
            return

        # No layer id: a pull-level event (Digest, Status, etc.).
        if "Digest" in status:
            self._aggregate_phase = _Phase.VERIFYING
        elif "Status: Downloaded" in status or "Status: Image is up to date" in status:
            self._aggregate_phase = _Phase.COMPLETE

    @staticmethod
    def _phase_for_status(status: str) -> _Phase:
        s = status.lower()
        if "pulling fs layer" in s:
            return _Phase.PULLING
        if "downloading" in s or "waiting" in s:
            return _Phase.DOWNLOADING
        if "verifying" in s or "checksum" in s:
            return _Phase.VERIFYING
        if "download complete" in s:
            return _Phase.DOWNLOADING  # still in download phase until extracting
        if "extracting" in s:
            return _Phase.EXTRACTING
        if "pull complete" in s:
            return _Phase.COMPLETE
        return _Phase.UNKNOWN

    def snapshot(self) -> DockerPullSnapshot:
        """Aggregate across all known layers.

        ``bytes_total`` is the sum of every layer's total (where known).
        ``bytes_done`` is the sum of every layer's current, plus any
        layers whose total is unknown but whose phase is ``COMPLETE``
        (treating them as 0 contribution — they were small manifests).
        """
        total = 0
        done = 0
        for layer in self._layers.values():
            if layer.total > 0:
                total += layer.total
                done += min(layer.current, layer.total)
        phase = _PHASE_LABELS.get(self._aggregate_phase, _PHASE_LABELS[_Phase.UNKNOWN])
        return DockerPullSnapshot(bytes_done=done, bytes_total=total, phase=phase)


__all__ = ["DockerPullProgress", "DockerPullSnapshot"]
