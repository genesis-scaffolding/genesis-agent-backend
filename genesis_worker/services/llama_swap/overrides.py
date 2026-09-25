"""Per-model user overrides on top of recipe defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ...contracts.host import ComputeDevice, CpuDevice, GpuDevice


def _serialize_value(value: Any) -> Any:
    """Convert a ComputeDevice to a yaml-friendly dict; pass everything else through.

    :class:`ComputeDevice` subclasses aren't basic yaml types, so
    :func:`yaml.safe_dump` raises :class:`yaml.representer.RepresenterError`
    when it encounters one. The wire format mirrors the load shape:

    - ``GpuDevice`` → ``{"vendor": ..., "index": ..., "label": ...}``
    - ``CpuDevice`` → ``{}`` (no vendor field; the absence is the discriminator)

    The cascade coerces back on the read path
    (:func:`genesis_worker.services.llama_swap.generate_config.evaluate_recipe`),
    so the round-trip is lossless.
    """
    if isinstance(value, ComputeDevice):
        if isinstance(value, CpuDevice):
            return {}
        if isinstance(value, GpuDevice):
            return {
                "vendor": value.vendor,
                "index": value.index,
                "label": value.label,
            }
    return value


def _serialize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_value(value) for key, value in fields.items()}


class OverridesStore:
    """Read/write ``overrides.yaml``.

    The store is intentionally tiny: load returns a dict, save writes
    a dict. Validation, field-level merging, and "is this a valid
    override for this entry" all live one layer up (in ``config.py``).

    ComputeDevice subclasses in the override values are serialised to
    plain dicts at save time (``_serialize_fields``); the load path
    returns the raw dict and consumers coerce back as needed.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        """Return ``{entry_id: {field: value, ...}}``. Missing file = empty."""
        if not self.path.is_file():
            return {}
        raw = yaml.safe_load(self.path.read_text()) or {}
        return raw.get("entries", {})

    def save(self, entries: dict[str, dict]) -> None:
        """Write the overrides dict back to disk. Creates parent dirs."""
        serializable = {entry_id: _serialize_fields(fields) for entry_id, fields in entries.items()}
        payload = {"entries": serializable}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(payload, sort_keys=False))


__all__ = ["OverridesStore"]
