"""Focused unit tests for :class:`LlamaSwapService` surface (paths, settings, helpers).

Lifecycle behavior is exercised in :mod:`test_lifecycle` against a fake
llama-swap shim. This module covers:

- path-resolution fallback chain (``config_path``, ``recipes_path``)
- ``last_generated_at`` reads the timestamp embedded by ``emit_payload``
- ``regenerate_config`` writes the embedded ``generated_at`` so
  ``is_config_stale`` flips correctly
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.services.llama_swap import LlamaSwapService
from genesis_worker.services.llama_swap.config import (
    is_config_stale,
    read_generated_at,
)
from genesis_worker.settings import (
    LlamaSwapServiceSettings,
    PathsSettings,
    Settings,
    SourcesSettings,
)

# ---------------------------------------------------------------------------
# config_path / recipes_path fallback chain
# ---------------------------------------------------------------------------


def test_config_path_falls_back_to_repo_root(tmp_path: Path) -> None:
    """When ``settings.config_path`` is None, fall back to ``<repo_root>/config.yaml``."""
    (tmp_path / "config.yaml").write_text("x: 1\n")
    svc = LlamaSwapService(
        settings=LlamaSwapServiceSettings(config_path=None, repo_root=tmp_path)
    )
    assert svc.config_path() == tmp_path / "config.yaml"


def test_config_path_explicit_setting_wins(tmp_path: Path) -> None:
    """Explicit ``settings.config_path`` wins over the repo-root fallback."""
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("x: 1\n")
    svc = LlamaSwapService(
        settings=LlamaSwapServiceSettings(config_path=explicit, repo_root=tmp_path)
    )
    assert svc.config_path() == explicit


def test_config_path_falls_back_to_xdg_when_no_repo_file(tmp_path: Path) -> None:
    """With no explicit setting and no repo-root file, returns the XDG default."""
    svc = LlamaSwapService(
        settings=LlamaSwapServiceSettings(
            config_path=None, repo_root=tmp_path, config_dir=tmp_path / "xdg"
        )
    )
    assert svc.config_path() == tmp_path / "xdg" / "services" / "llama-swap" / "config.yaml"


def test_recipes_path_falls_back_to_repo_root(tmp_path: Path) -> None:
    (tmp_path / "recipes.yaml").write_text("x: 1\n")
    svc = LlamaSwapService(
        settings=LlamaSwapServiceSettings(repo_root=tmp_path)
    )
    assert svc.recipes_path() == tmp_path / "recipes.yaml"


def test_overrides_path_lives_next_to_config_path(tmp_path: Path) -> None:
    """``overrides.yaml`` is co-located with ``config.yaml``."""
    cfg = tmp_path / "my-config.yaml"
    svc = LlamaSwapService(settings=LlamaSwapServiceSettings(config_path=cfg))
    assert svc.overrides_path() == tmp_path / "overrides.yaml"


# ---------------------------------------------------------------------------
# config-stale detection
# ---------------------------------------------------------------------------


def test_read_generated_at_returns_field(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("healthCheckTimeout: 60\ngenerated_at: '2026-08-10T00:00:00+00:00'\nroot: x\nmodels: {}\n")
    assert read_generated_at(cfg) == "2026-08-10T00:00:00+00:00"


def test_read_generated_at_recognizes_legacy_comment_header(tmp_path: Path) -> None:
    """``bin/build-config.py`` writes the timestamp as a YAML comment, not a field.

    Until Phase 10 retirement retires that script, ``read_generated_at``
    must also parse ``# llama-swap config generated <ts>`` so the
    staleness indicator works against existing on-disk configs.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "# llama-swap config generated 2026-08-10T00:00:00+00:00\n"
        "# root: /tmp/x\n"
        "healthCheckTimeout: 60\n"
        "logLevel: info\n"
        "models: {}\n"
    )
    assert read_generated_at(cfg) == "2026-08-10T00:00:00+00:00"


def test_read_generated_at_prefers_yaml_field_over_comment(tmp_path: Path) -> None:
    """When both forms are present, the YAML field wins (newer writer)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "# llama-swap config generated 2026-08-10T00:00:00+00:00\n"
        "generated_at: '2026-08-11T00:00:00+00:00'\n"
        "models: {}\n"
    )
    assert read_generated_at(cfg) == "2026-08-11T00:00:00+00:00"


def test_read_generated_at_returns_none_when_field_missing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("healthCheckTimeout: 60\nmodels: {}\n")  # no generated_at
    assert read_generated_at(cfg) is None


def test_read_generated_at_returns_none_when_missing_file(tmp_path: Path) -> None:
    assert read_generated_at(tmp_path / "absent.yaml") is None


def test_read_generated_at_returns_none_when_malformed(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(": not valid yaml :: :::\n")
    assert read_generated_at(cfg) is None


def test_is_config_stale_when_timestamp_differs(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("generated_at: 2026-08-10T00:00:00+00:00\nmodels: {}\n")
    assert is_config_stale(cfg, catalog_generated_at="2026-08-10T00:00:01+00:00") is True


def test_is_config_stale_false_when_timestamp_matches(tmp_path: Path) -> None:
    ts = "2026-08-10T00:00:00+00:00"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"generated_at: '{ts}'\nmodels: {{}}\n")
    assert is_config_stale(cfg, catalog_generated_at=ts) is False


def test_is_config_stale_true_when_field_missing(tmp_path: Path) -> None:
    """Older config.yaml files lack ``generated_at``; treat as stale."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("models: {}\n")
    assert is_config_stale(cfg, catalog_generated_at="2026-08-10T00:00:00+00:00") is True


# ---------------------------------------------------------------------------
# regenerate_config writes the timestamp; last_generated_at reads it
# ---------------------------------------------------------------------------


def test_regenerate_config_writes_generated_at_and_no_longer_stale(tmp_path: Path) -> None:
    """End-to-end: regenerate against a fresh catalog -> not stale.

    The service is now a pure function — it receives catalog, paths,
    and overrides from the caller and produces config. No worker
    dependency, no path resolution inside the service.
    """
    cfg = tmp_path / "config.yaml"
    recipes_path = tmp_path / "recipes.yaml"
    recipes_path.write_text(
        "recipes:\n"
        "  default:\n"
        "    match:\n"
        "    ctx_min: 1024\n"
    )

    # Build a worker pointed at an empty vault (to produce a catalog).
    settings = Settings(
        paths=PathsSettings(vault_path=tmp_path),
        sources=SourcesSettings(),
    )
    from genesis_worker import GenesisWorker

    worker = GenesisWorker(settings=settings)
    catalog = worker.rescan_catalog()

    # The service is now stateless regarding config generation — it
    # receives everything it needs as parameters.
    svc = LlamaSwapService(
        settings=LlamaSwapServiceSettings(
            config_path=cfg,
            recipes_path=recipes_path,
            repo_root=tmp_path,
            config_dir=tmp_path,
            log_dir=tmp_path,
        ),
    )
    assert svc.regenerate_config(
        catalog=catalog,
        config_path=cfg,
        recipes_path=recipes_path,
    ) is True

    # The file must exist and contain the fresh timestamp.
    assert cfg.is_file()
    embedded = read_generated_at(cfg)
    assert embedded is not None
    assert embedded == catalog.generated_at
    # And not stale against the same rescan.
    assert svc.last_generated_at() == embedded
    assert is_config_stale(cfg, catalog_generated_at=embedded) is False


def test_last_generated_at_returns_none_when_config_absent(tmp_path: Path) -> None:
    svc = LlamaSwapService(
        settings=LlamaSwapServiceSettings(
            config_path=tmp_path / "missing.yaml", repo_root=tmp_path
        ),
    )
    assert svc.last_generated_at() is None


# ---------------------------------------------------------------------------
# Make sure resource_estimate still returns the spec placeholder
# ---------------------------------------------------------------------------


def test_resource_estimate_returns_spec_placeholder() -> None:
    """Spec-002 placeholder values; not zeroed in v1."""
    from genesis_worker.services._base import ServiceResourceEstimate

    svc = LlamaSwapService()
    est = svc.resource_estimate()
    assert isinstance(est, ServiceResourceEstimate)
    assert est.vram_bytes_typical == 5_000_000_000
    assert est.vram_bytes_min == 2_000_000_000
    assert est.cpu_cores_recommended == 4