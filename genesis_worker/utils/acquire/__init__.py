"""Acquire session utilities for install flavors.

Each module in this package provides a :class:`BackgroundSession`
subclass that drives one install flavor: GitHub release tarball,
``docker pull``, ``uv tool install``. ``MultiInstallSession`` composes
several of them into one session (ADR-049). Services import the session
they need and pass plugin-specific configuration.

See ADR-028.
"""

from .docker_pull import DockerPullAcquireSession
from .github_release import (
    GithubReleaseAcquireSession,
    GithubReleaseTarball,
    fetch_latest_release,
)
from .multi_install import MultiInstallSession
from .uv_tool import UvToolAcquireSession

__all__ = [
    "DockerPullAcquireSession",
    "GithubReleaseAcquireSession",
    "GithubReleaseTarball",
    "MultiInstallSession",
    "UvToolAcquireSession",
    "fetch_latest_release",
]
