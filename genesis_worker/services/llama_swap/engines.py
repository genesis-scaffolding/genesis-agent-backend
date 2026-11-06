"""Inference engine kinds supported by llama-swap."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class Engine(StrEnum):
    LLAMA_SERVER = "llama_server"
    RADIANCE = "radiance"


def engine_for_file(path: Path | None) -> Engine:
    """A ``.rad`` file is a radiance weight container; anything else is a llama-server GGUF."""
    if path is not None and path.suffix.lower() == ".rad":
        return Engine.RADIANCE
    return Engine.LLAMA_SERVER


__all__ = ["Engine", "engine_for_file"]
