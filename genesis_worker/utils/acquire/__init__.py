"""Acquire session utilities for install flavors.

Each module in this package provides a :class:`BackgroundSession`
subclass that drives one install flavor: GitHub release tarball,
``docker pull``, ``uv tool install``. Services import the session
they need and pass plugin-specific configuration.

See ADR-028.
"""

from .docker_pull import DockerPullAcquireSession
from .github_release import GithubReleaseAcquireSession, GithubReleaseTarball
from .uv_tool import UvToolAcquireSession

__all__ = [
    "DockerPullAcquireSession",
    "GithubReleaseAcquireSession",
    "GithubReleaseTarball",
    "UvToolAcquireSession",
]
