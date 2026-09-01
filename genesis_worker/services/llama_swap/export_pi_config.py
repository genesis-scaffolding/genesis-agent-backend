"""Build the pi-agent ``models.json`` payload from structured ``EvaluatedConfig``.

The exporter reads straight off the dataclass — there is no longer a
fallback that parses the rendered llama-server cmd string with regexes.
``evaluate_all`` is the single source of truth for both the cmd
written to ``config.yaml`` and the fields emitted here.
"""

from __future__ import annotations

import json
import os
import re
import socket
from collections.abc import Mapping

from ...contracts import Catalog
from .generate_config import EvaluatedConfig

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
# 84k is the fallback when neither ctx_size nor ctx_min was set on the
# model. Lower than llama.cpp's full default (131072) so a
# pi-agent client that asks for a long response doesn't silently hit
# a model that wasn't actually configured for it. Models that can do
# more carry their own ctx_size / ctx_min and the export picks them up.
DEFAULT_CONTEXT_WINDOW = 84000
DEFAULT_MAX_TOKENS = 16384
FALLBACK_PROVIDER_NAME = "llama-swap"

INSTRUCT_TOKEN = "instruct"


def build_provider_from_configs(
    configs: Mapping[str, EvaluatedConfig],
    *,
    base_url: str | None = None,
    hostname: str | None = None,
) -> dict:
    """Build the pi-agent ``models.json`` payload from a dict of evaluated configs.

    The caller supplies the structured configs (typically
    ``service.evaluate_model_config(catalog)``). The same data drives
    ``config.yaml``; the two paths cannot drift.
    """
    provider_name = _resolve_hostname(hostname)
    base = _resolve_base_url(base_url)
    models = [_build_model(entry_id, cfg) for entry_id, cfg in configs.items()]
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


def build_provider_for_catalog(
    catalog: Catalog,
    *,
    evaluate,  # callable: (Catalog) -> Mapping[str, EvaluatedConfig]
    base_url: str | None = None,
    hostname: str | None = None,
) -> dict:
    """Convenience wrapper for callers that already hold a catalog.

    ``evaluate`` is the catalog -> configs mapping the caller wants to
    use (typically ``service.evaluate_model_config``). Splitting the
    lookup this way lets the service own its evaluation pipeline
    without the exporter importing generation internals.
    """
    return build_provider_from_configs(
        evaluate(catalog),
        base_url=base_url,
        hostname=hostname,
    )


def write_models_json(target, provider: dict) -> bool:
    """Write ``provider`` as JSON to ``target`` iff contents differ.

    Returns True when a write occurred. Preserves mtime on no-op writes
    so consumers watching the file (or copying it elsewhere) don't see
    a spurious change.
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


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_model(entry_id: str, cfg: EvaluatedConfig) -> dict:
    return {
        "id": entry_id,
        "name": cfg.name,
        "input": ["text", "image"] if cfg.files.mmproj else ["text"],
        # Priority: ctx_size (cap) > ctx_min (floor) > fallback.
        # The caller has already decided what gets emitted to the cmd;
        # pi just needs a single number.
        "contextWindow": _context_window(cfg),
        "maxTokens": DEFAULT_MAX_TOKENS,
        "reasoning": _reasoning(entry_id, cfg),
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }


def _context_window(cfg: EvaluatedConfig) -> int:
    if cfg.ctx_size is not None:
        return cfg.ctx_size
    if cfg.ctx_min is not None:
        return cfg.ctx_min
    return DEFAULT_CONTEXT_WINDOW


def _reasoning(entry_id: str, cfg: EvaluatedConfig) -> bool:
    """False only when thinking is explicitly disabled or the id says instruct."""
    ctk = cfg.chat_template_kwargs or {}
    if ctk.get("enable_thinking") is False:
        return False
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
    # Fall back to the worker's hostname so pi-agent on a different
    # machine reaches the worker over the LAN/VPN. ``127.0.0.1`` would
    # point at the calling machine, not the worker.
    try:
        host = socket.gethostname()
    except OSError:
        host = "localhost"
    return _norm(f"http://{host}:8080")


def _norm(url: str) -> str:
    return url if url.endswith("/v1") else url.rstrip("/") + "/v1"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or FALLBACK_PROVIDER_NAME


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CONTEXT_WINDOW",
    "build_provider_for_catalog",
    "build_provider_from_configs",
    "write_models_json",
]
