"""The compose env file — the user's env map reaches the container (ADR-053).

A compose template can only name the environment variables it knows, so
the long tail of upstream env vars has no place in it. The framework
writes the user's ``extra_env`` map to a file it manages and the template
points at that file with compose's ``env_file:``.

These tests cover the writer (quoting, mode, atomicity) and the compose
service (the file exists before ``up`` runs).
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest

from genesis_worker.contracts import StartResult
from genesis_worker.tests._factories import service_ctx
from genesis_worker.utils.services import ComposeService, load_service_spec
from genesis_worker.utils.services.compose_stack import DockerComposeStack
from genesis_worker.utils.services.env_file import write_env_file

_DECLARATIVE_DIR = Path(__file__).resolve().parent.parent / "services" / "_declarative"


# --- the writer -------------------------------------------------------------


def test_write_env_file_creates_a_compose_readable_file(tmp_path: Path) -> None:
    """The file holds one ``KEY="value"`` line per entry."""
    target = tmp_path / "romm.env"
    write_env_file(target, {"SCREENSCRAPER_USER": "gen", "SCREENSCRAPER_PASSWORD": "hunter2"})
    lines = target.read_text().splitlines()
    assert lines == ['SCREENSCRAPER_USER="gen"', 'SCREENSCRAPER_PASSWORD="hunter2"']


def test_write_env_file_escapes_quotes_backslashes_and_dollar(tmp_path: Path) -> None:
    """Every special character is escaped — compose unescapes exactly these.

    Compose's env-file parser reads a double-quoted value like a shell:
    ``\\\\`` is a backslash, ``\\"`` a quote, ``\\$`` a dollar sign. An
    unescaped ``$`` would be expanded by compose; an unescaped quote
    would end the value; an unescaped backslash before a quote would
    escape it.
    """
    target = tmp_path / "romm.env"
    write_env_file(target, {"K": "a'b\"c$d\\e #f"})
    assert target.read_text() == 'K="a\'b\\"c\\$d\\\\e #f"\n'


def test_write_env_file_keeps_a_hash_and_spaces_inside_the_value(tmp_path: Path) -> None:
    """An inline comment only exists outside a quoted value."""
    target = tmp_path / "romm.env"
    write_env_file(target, {"K": "pass word # not a comment"})
    assert target.read_text() == 'K="pass word # not a comment"\n'


def test_write_env_file_is_0600_and_atomic(tmp_path: Path) -> None:
    """The file is private (it carries credentials) and leaves no tmp file."""
    target = tmp_path / "romm.env"
    write_env_file(target, {"K": "v"})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["romm.env"]


def test_write_env_file_overwrites_the_previous_content(tmp_path: Path) -> None:
    """A stale key from a previous run is gone — the file is rewritten."""
    target = tmp_path / "romm.env"
    write_env_file(target, {"OLD": "1", "KEEP": "2"})
    write_env_file(target, {"KEEP": "3"})
    assert target.read_text() == 'KEEP="3"\n'


def test_write_env_file_rejects_an_invalid_variable_name(tmp_path: Path) -> None:
    """A name compose cannot parse fails loudly instead of breaking the stack."""
    with pytest.raises(ValueError, match="invalid environment variable name"):
        write_env_file(tmp_path / "romm.env", {"NOT A NAME": "v"})


# --- the compose service ----------------------------------------------------


def _romm_service(tmp_path: Path, options: dict[str, Any] | None = None) -> ComposeService:
    svc = load_service_spec(
        _DECLARATIVE_DIR / "romm.yaml",
        ctx=service_ctx(tmp_path, name="romm", options=options or {}),
    )
    assert isinstance(svc, ComposeService)
    return svc


def test_compose_service_writes_the_env_file_before_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env file exists — with the user's map — when compose comes up."""
    monkeypatch.setattr(
        DockerComposeStack,
        "pull",
        lambda self: StartResult(ok=True, message="pulled"),
    )

    svc = _romm_service(
        tmp_path,
        options={"extra_env": {"SCREENSCRAPER_USER": "gen", "SCREENSCRAPER_PASSWORD": "hunter2"}},
    )
    env_file = svc.config.env_file
    assert env_file is not None

    seen: dict[str, Any] = {}

    def _up(self: DockerComposeStack, *, env: dict[str, str] | None = None) -> StartResult:
        seen["exists"] = env_file.is_file()
        seen["body"] = env_file.read_text() if seen["exists"] else ""
        seen["process_env"] = env
        return StartResult(ok=True, message="up")

    monkeypatch.setattr(DockerComposeStack, "up", _up)

    result = svc.start()
    assert result.ok, result.message
    assert seen["exists"] is True
    assert seen["body"] == 'SCREENSCRAPER_USER="gen"\nSCREENSCRAPER_PASSWORD="hunter2"\n'
    # The user's map also stays in the compose process environment, so a
    # template may forward a named variable from it.
    assert seen["process_env"]["SCREENSCRAPER_USER"] == "gen"


def test_compose_service_without_env_file_does_not_write_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spec without an ``env_file`` writes no env file."""
    monkeypatch.setattr(
        DockerComposeStack,
        "pull",
        lambda self: StartResult(ok=True, message="pulled"),
    )
    monkeypatch.setattr(
        DockerComposeStack,
        "up",
        lambda self, *, env=None: StartResult(ok=True, message="up"),
    )

    spec = tmp_path / "plain.yaml"
    spec.write_text(
        "version: 1\n"
        "kind: compose\n"
        "name: plain\n"
        "display_name: Plain\n"
        "description: plain service\n"
        "category: utility\n"
        "capabilities:\n"
        "  can_generate_config: false\n"
        "  can_export_for_agent: false\n"
        "  can_serve_llm: false\n"
        "  can_serve_image: false\n"
        "  can_train_models: false\n"
        "  has_web_ui: true\n"
        "  can_install: true\n"
        "resource_estimate:\n"
        "  vram_bytes_typical: 0\n"
        "  vram_bytes_min: 0\n"
        "  cpu_cores_recommended: 1\n"
        "images:\n"
        "  - name: app\n"
        "    repo: alpine\n"
        "    default_tag: '3'\n"
        "compose_template: |\n"
        '  version: "3.8"\n'
        "  services:\n"
        "    app:\n"
        "      image: $images.app\n"
        "options:\n"
        "  listen_port:\n"
        "    type: port\n"
        "    default: 8099\n"
    )
    svc = load_service_spec(spec, ctx=service_ctx(tmp_path, name="plain"))
    assert isinstance(svc, ComposeService)
    assert svc.config.env_file is None

    result = svc.start()
    assert result.ok, result.message
    assert list(tmp_path.rglob("*.env")) == []
