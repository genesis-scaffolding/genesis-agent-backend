"""Build pi-agent ``models.json`` from a llama-swap ``config.yaml``."""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path

import yaml

# Defaults match the old bin/pi-models.py verbatim.
DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_CONTEXT_WINDOW = 131072
DEFAULT_MAX_TOKENS = 16384
FALLBACK_PROVIDER_NAME = "llama-swap"

# --fit-ctx N (with optional `=`). We pick the LAST occurrence in case
# some recipe repeats the flag.
FIT_CTX_RE = re.compile(r"--fit-ctx(?:=|\s+)(\d+)")
MMPROJ_RE = re.compile(r"(?m)(?:^|\s)--mmproj(?:\s|\=)")
CHAT_TEMPLATE_KWARGS_RE = re.compile(r"--chat-template-kwargs\s+'([^']+)'")
INSTRUCT_TOKEN = "instruct"


def build_provider(
    config_path: Path,
    *,
    base_url: str | None = None,
    hostname: str | None = None,
) -> dict:
    """Read ``config.yaml`` and return the pi-agent ``models.json`` payload."""
    raw = yaml.safe_load(config_path.read_text())
    models_cfg = (raw or {}).get("models", {}) or {}
    provider_name = _resolve_hostname(hostname)
    base = _resolve_base_url(base_url)
    models = [_build_model(entry_id, body) for entry_id, body in models_cfg.items()]
    return {
        "providers": {
            provider_name: {
                "baseUrl": base,
                "api": "openai-completions",
                "apiKey": "local",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                    "maxTokensField": "max_tokens",
                },
                "models": models,
            }
        }
    }


def write_models_json(target: Path, provider: dict) -> bool:
    """Write ``provider`` as JSON to ``target`` iff contents differ.

    Returns True when a write occurred. Preserves mtime on no-op writes
    so consumers watching the file (or copying it elsewhere) don't see
    a spurious change. Mirrors the safety the old
    ``bin/pi-models.py`` had via ``_write_if_changed``.
    """
    payload = json.dumps(provider, indent=2, sort_keys=True) + "\n"
    try:
        existing = target.read_text()
    except FileNotFoundError:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload)
        return True
    if existing == payload:
        return False
    target.write_text(payload)
    return True


def default_target_path(base_url: str | None = None) -> Path:
    """The default ``pi-models.json`` location used by the CLI.

    Resolved relative to CWD so the CLI behaves the same as
    ``bin/pi-models.py`` did (writing ``./pi-models.json``).
    """
    return Path.cwd() / "pi-models.json"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_model(entry_id: str, body: dict) -> dict:
    cmd = body.get("cmd", "") or ""
    return {
        "id": entry_id,
        "name": body.get("name", entry_id),
        "input": ["text", "image"] if MMPROJ_RE.search(cmd) else ["text"],
        "contextWindow": _ctx(cmd),
        "maxTokens": DEFAULT_MAX_TOKENS,
        "reasoning": _reasoning(entry_id, cmd),
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }


def _ctx(cmd: str) -> int:
    matches = FIT_CTX_RE.findall(cmd)
    if matches:
        try:
            return int(matches[-1])
        except ValueError:
            pass
    return DEFAULT_CONTEXT_WINDOW


def _reasoning(entry_id: str, cmd: str) -> bool:
    """False only when thinking is explicitly disabled or the id says instruct."""
    match = CHAT_TEMPLATE_KWARGS_RE.search(cmd)
    if match:
        try:
            if json.loads(match.group(1)).get("enable_thinking") is False:
                return False
        except json.JSONDecodeError:
            pass
    return INSTRUCT_TOKEN not in entry_id.lower()


def _resolve_hostname(explicit: str | None) -> str:
    if explicit:
        return _slug(explicit)
    try:
        return _slug(socket.gethostname())
    except OSError:
        return FALLBACK_PROVIDER_NAME


def _resolve_base_url(explicit: str | None) -> str:
    if explicit:
        return _norm(explicit)
    for env in ("PI_BASE_URL", "LLAMA_BASE_URL"):
        v = os.environ.get(env)
        if v:
            return _norm(v)
    return DEFAULT_BASE_URL


def _norm(url: str) -> str:
    return url if url.endswith("/v1") else url.rstrip("/") + "/v1"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or FALLBACK_PROVIDER_NAME


__all__ = [
    "DEFAULT_BASE_URL",
    "build_provider",
    "default_target_path",
    "write_models_json",
]

