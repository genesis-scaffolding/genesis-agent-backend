"""Tests for ``utils.registry_tags`` — registry listing with a releases fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genesis_worker.utils import registry_tags
from genesis_worker.utils.registry_tags import (
    list_image_tags,
    read_tag_cache,
    release_tags,
    tag_cache_path,
    write_tag_cache,
)


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Resp:  # noqa: PYI034
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def read(self) -> bytes:
        return self._body


def _fake_urlopen(payload: object) -> object:
    def _urlopen(req, **kw):  # type: ignore[no-untyped-def]
        return _Resp(json.dumps(payload).encode())

    return _urlopen


def _patch_releases(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))


def _patch_registry(monkeypatch: pytest.MonkeyPatch, tags: list[str]) -> None:
    from genesis_worker.utils.process import DockerContainer

    monkeypatch.setattr(DockerContainer, "list_remote_tags", staticmethod(lambda repo: list(tags)))


# --- release fallback -------------------------------------------------------


def test_release_tags_parsed_from_release_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_releases(
        monkeypatch,
        [
            {
                "body": (
                    "| `ghcr.io/genesis-scaffolding/comfyui-rocm:v0.43.0-rocm-7.14-amd64` | x |\n"
                    "| `ghcr.io/genesis-scaffolding/comfyui-rocm:latest-rocm-7.14-amd64` | x |"
                )
            },
            {"body": "| `ghcr.io/genesis-scaffolding/comfyui-rocm:v0.42.1-rocm-7.2-amd64` | x |"},
        ],
    )
    tags = release_tags("ghcr.io/genesis-scaffolding/comfyui-rocm")
    assert tags == [
        "v0.43.0-rocm-7.14-amd64",
        "latest-rocm-7.14-amd64",
        "v0.42.1-rocm-7.2-amd64",
    ]


def test_release_tags_ignore_other_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_releases(
        monkeypatch,
        [{"body": "| `ghcr.io/genesis-scaffolding/comfyui-cuda:v0.43.0-cuda-13.0-amd64` | x |"}],
    )
    assert release_tags("ghcr.io/genesis-scaffolding/comfyui-rocm") == []


def test_release_tags_skip_non_ghcr_repos() -> None:
    assert release_tags("photoprism/photoprism") == []
    assert release_tags("ghcr.io/no-owner") == []


def test_release_tags_tolerate_a_failing_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(req, **kw):  # type: ignore[no-untyped-def]
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert release_tags("ghcr.io/genesis-scaffolding/comfyui-rocm") == []


# --- list_image_tags --------------------------------------------------------


def test_list_image_tags_prefers_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, ["v0.43.0-rocm-7.14-amd64"])
    _patch_releases(monkeypatch, [{"body": "should not be consulted"}])
    assert list_image_tags("ghcr.io/genesis-scaffolding/comfyui-rocm") == [
        "v0.43.0-rocm-7.14-amd64"
    ]


def test_list_image_tags_falls_back_to_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, [])
    _patch_releases(
        monkeypatch,
        [{"body": "| `ghcr.io/genesis-scaffolding/comfyui-rocm:latest-rocm-7.14-amd64` | x |"}],
    )
    assert list_image_tags("ghcr.io/genesis-scaffolding/comfyui-rocm") == ["latest-rocm-7.14-amd64"]


def test_list_image_tags_empty_when_both_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, [])
    _patch_releases(monkeypatch, [])
    assert list_image_tags("ghcr.io/genesis-scaffolding/comfyui-rocm") == []


# --- cache ------------------------------------------------------------------


def test_tag_cache_roundtrip(tmp_path: Path) -> None:
    path = tag_cache_path(tmp_path, "ghcr.io/genesis-scaffolding/comfyui-rocm")
    write_tag_cache(path, ["a", "b"])
    assert read_tag_cache(path, 60) == ["a", "b"]


def test_tag_cache_expires(tmp_path: Path) -> None:
    path = tag_cache_path(tmp_path, "example/svc")
    write_tag_cache(path, ["a"])
    assert read_tag_cache(path, 0) is None
    assert read_tag_cache(path, None) == ["a"]


def test_tag_cache_ignores_garbage(tmp_path: Path) -> None:
    path = tag_cache_path(tmp_path, "example/svc")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    assert read_tag_cache(path, 60) is None
    assert registry_tags.tag_cache_path(tmp_path, "example/svc") == path
