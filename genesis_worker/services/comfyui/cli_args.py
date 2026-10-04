"""Shell-style tokeniser for the ComfyUI extra-args text area.

The container's argv reaches the entrypoint verbatim — each list element
becomes one argv element, no shell splitting. So a value like
``--reserve-vram 10`` typed on one line must become *two* argv tokens,
``["--reserve-vram", "10"]``, not one token with a literal space
(``"--reserve-vram 10"``) that the entrypoint's argparse would treat as
an unknown flag. This module exists because Streamlit page modules can't
be imported without a running app (top-level ``st.session_state`` reads
block a plain ``import``), and the parser deserves a unit test.
"""

from __future__ import annotations

import shlex


def parse_extra_args(raw: str) -> list[str]:
    """Parse the text area into a list of CLI tokens, one per argv element.

    Each non-blank line is shell-tokenised: ``--reserve-vram 10`` on
    one line becomes ``["--reserve-vram", "10"]``; ``--gpu-only``
    becomes ``["--gpu-only"]``; ``--cache-lru 8`` becomes
    ``["--cache-lru", "8"]``. ``shlex.split`` (posix mode) handles
    quoted values too (``--name "my workflow"`` → three tokens) so
    operators can include spaces inside a flag value if a flag ever
    needs them. Blank lines and lines that are only whitespace
    contribute nothing.

    Tokenising per line (rather than ``shlex.split(raw)``) preserves
    the "one flag per line" mental model the UI exposes, while still
    allowing a flag with its value on one line.
    """
    out: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        out.extend(shlex.split(line, posix=True))
    return out


__all__ = ["parse_extra_args"]
