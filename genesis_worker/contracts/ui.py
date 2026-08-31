"""UI contract — pages a plugin contributes to the management UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UiPage:
    """A page a plugin contributes to the management UI.

    Streamlit executes the file at ``path`` as a script. The page reads
    ``st.session_state["worker"]`` to access the cached facade.

    ``url_path`` is the page's URL pathname. ``None`` (default) lets
    Streamlit infer it from the filename — fine when the basename is
    globally unique. Set it explicitly when two pages in different
    services would otherwise share a basename (e.g. ``status.py`` on
    both llama_swap and cptr would both infer ``status`` and collide).
    """

    label: str  # sidebar text
    icon: str  # streamlit icon identifier (e.g. ":material/tune:")
    path: Path  # absolute path to the .py file
    url_path: str | None = None  # inferred from filename when None


__all__ = ["UiPage"]
