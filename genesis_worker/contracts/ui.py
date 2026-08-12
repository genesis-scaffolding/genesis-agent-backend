"""UI contract — pages a plugin contributes to the management UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UiPage:
    """A page a plugin contributes to the management UI.

    Streamlit executes the file at ``path`` as a script. The page reads
    ``st.session_state["worker"]`` to access the cached facade.
    """

    label: str          # sidebar text
    icon: str           # streamlit icon identifier (e.g. ":material/tune:")
    path: Path          # absolute path to the .py file


__all__ = ["UiPage"]