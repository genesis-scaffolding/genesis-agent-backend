"""Secrets accessor contract — plugins ask the framework for secrets by name (ADR-009, ADR-012)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class SecretsAccessor(ABC):
    """Read access to framework-managed secrets.

    The framework owns the secret storage (settings, env, .env). Plugins
    ask for a secret by name (e.g. ``github_token``) and never reach into
    ``os.environ`` or ``.env`` directly.
    """

    @abstractmethod
    def get(self, name: str) -> str | None: ...


class NoSecretsAccessor(SecretsAccessor):
    """Default accessor that returns ``None`` for everything.

    Used by tests and by plugin contexts that don't need secrets.
    """

    def get(self, name: str) -> str | None:
        return None


class StaticSecretsAccessor(SecretsAccessor):
    """An accessor backed by a fixed dict. Useful for tests."""

    def __init__(self, data: Mapping[str, str]) -> None:
        self._data = dict(data)

    def get(self, name: str) -> str | None:
        return self._data.get(name)


__all__ = [
    "NoSecretsAccessor",
    "SecretsAccessor",
    "StaticSecretsAccessor",
]
