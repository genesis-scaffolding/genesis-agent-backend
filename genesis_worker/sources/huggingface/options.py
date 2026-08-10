"""Options this source accepts under ``settings.sources.huggingface``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class HuggingFaceOptions(BaseModel):
    # local_path is resolved by the framework against the vault; see SourceContext.
    local_path: Path | None = None
    default_revision: str = "main"


__all__ = ["HuggingFaceOptions"]
