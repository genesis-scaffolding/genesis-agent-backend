"""Acquire session for cptr — installs via uv, then patches hardcoded timeout."""

from __future__ import annotations

import os
import site
from pathlib import Path
from typing import Any

from ...contracts import AcquireStateKind
from ...utils.acquire import UvToolAcquireSession

_PI_TIMEOUT_OLD = "timeout=120"
_PI_TIMEOUT_NEW = "timeout=900"


def patch_pi_timeout() -> bool:
    """Patch cptr/utils/agents/pi.py if not already patched.

    See :ref:`docs/cptr-timeout-patch` for why this is needed.
    Replaces ``timeout=120`` with ``timeout=900`` on the pi-agent event-wait
    call, which otherwise fires too early for local GPU inference workloads.
    Idempotent: returns False if the file is absent or already patched.
    """
    pi_file = _find_pi_file()
    if pi_file is None:
        return False
    text = pi_file.read_text()
    if _PI_TIMEOUT_OLD not in text:
        return False
    pi_file.write_text(text.replace(_PI_TIMEOUT_OLD, _PI_TIMEOUT_NEW))
    return True


def _find_pi_file() -> Any:
    """Find cptr/utils/agents/pi.py in site-packages.

    Searches the current Python's site-packages (normal installs) and the
    uv-tool isolated env for the named package.
    """
    candidates: list[Any] = []
    for sp in site.getsitepackages():
        p = Path(sp) / "cptr" / "utils" / "agents" / "pi.py"
        if p.is_file():
            candidates.append(p)
    # Also check the uv-tool isolated env (~/.local/share/uv/tools/<name>/).
    uv_tool_dir = Path.home() / ".local" / "share" / "uv" / "tools" / "cptr" / "lib"
    if uv_tool_dir.is_dir():
        for child in uv_tool_dir.iterdir():
            if child.is_dir() and child.name.startswith("python"):
                p = child / "site-packages" / "cptr" / "utils" / "agents" / "pi.py"
                if p.is_file():
                    candidates.append(p)
    for p in candidates:
        if os.access(p, os.W_OK):
            return p
    return candidates[0] if candidates else None


class CptrAcquireSession(UvToolAcquireSession):
    """Installs cptr via uv, then patches the hardcoded 120s pi-agent timeout.

    See :ref:`docs/cptr-timeout-patch` for the full context.
    """

    def _post_run_hook(self) -> None:
        if self._state.kind != AcquireStateKind.COMPLETE:
            return
        try:
            if patch_pi_timeout():
                self._append_log(
                    f"[cptr acquire] patched cptr/utils/agents/pi.py: "
                    f"{_PI_TIMEOUT_OLD} -> {_PI_TIMEOUT_NEW}"
                )
        except Exception as exc:  # noqa: BLE001 — log and continue
            self._append_log(f"[cptr acquire] patch failed: {exc}")


__all__ = ["CptrAcquireSession", "patch_pi_timeout"]
