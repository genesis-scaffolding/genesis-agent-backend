"""Helpers for navigation between framework pages."""

from __future__ import annotations

import os.path
from pathlib import Path

# st.switch_page resolves paths relative to the main app script's directory,
# which is genesis_worker/ui/app.py. This file lives at
# genesis_worker/utils/ui/_nav.py, so the main app's directory is two
# levels up from here, then into the "ui" directory.
MAIN_APP_DIR = Path(__file__).parents[2] / "ui"


def to_relative(page_path: Path) -> str:
    """Return ``page_path`` as a path string relative to the main script's dir.

    ``st.switch_page`` requires a file path relative to the directory of the
    main app script. ``Path.relative_to`` refuses ``..`` segments, so we
    use ``os.path.relpath`` which handles sibling directories like
    ``../services/llama_swap/ui/status.py``.
    """
    return os.path.relpath(str(page_path), start=str(MAIN_APP_DIR))
