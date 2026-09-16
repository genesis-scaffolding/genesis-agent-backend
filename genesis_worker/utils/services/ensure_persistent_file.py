"""Read-or-create a persistent token file.

Used by the ``ensure_persistent_token`` pre-start hook (and any future
caller that wants the same idempotent read-or-generate behaviour):

- If ``target`` exists with non-empty content, return that content.
- Otherwise call ``generator()``, write it atomically with ``mode``,
  and return the new value.

The atomic-write pattern (write to ``target.tmp.<rand>`` then
``os.replace``) is the same one used by SillyTavern's config-seed and
the crawl4ai token generator, so an interrupted write can't leave a
half-written token in place.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path


def random_hex_32() -> str:
    """64 hex chars (256 bits) of entropy."""
    return secrets.token_hex(32)


def random_urlsafe_32() -> str:
    """~43 chars, url-safe-base64 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def ensure_persistent_file(
    target: Path,
    *,
    mode: int = 0o600,
    generator: Callable[[], str] | None = None,
) -> str:
    """Return ``target``'s contents; create+populate it when absent or empty.

    ``mode`` is applied atomically with the final ``os.replace`` (via
    ``chmod`` on the temp file). The parent directory is created on
    demand. ``generator`` defaults to :func:`random_hex_32` when None.
    """
    if target.is_file():
        existing = target.read_text().strip()
        if existing:
            return existing

    gen = generator or random_hex_32
    value = gen()

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    with tmp.open("w") as f:
        f.write(value)
    tmp.chmod(mode)
    os.replace(tmp, target)
    return value


__all__ = [
    "ensure_persistent_file",
    "random_hex_32",
    "random_urlsafe_32",
]
