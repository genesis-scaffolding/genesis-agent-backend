"""The ref an installable actually provides — one place, so every surface agrees.

The status page's "still needed" caption, the composite install session
and the Images page must all name the same artifact. The name comes
from the installable: its effective ref (a docker image at the version
it will run), else its image ref, else its own name.
"""

from __future__ import annotations

from ..contracts import ServiceInstall


def installable_ref(installable: ServiceInstall) -> str:
    """The image ref an installable provides, at the version it will run.

    ``effective_ref`` when the installable has one (docker images: the
    recorded selection over the default tag), else its ``image_ref``,
    else its name.
    """
    ref = getattr(installable, "effective_ref", None)
    if isinstance(ref, str) and ref:
        return ref
    base = getattr(installable, "image_ref", None)
    if isinstance(base, str) and base:
        return base
    return installable.name


__all__ = ["installable_ref"]
