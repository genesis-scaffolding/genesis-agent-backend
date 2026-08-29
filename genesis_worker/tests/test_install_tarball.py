"""Tests for the GithubReleaseTarball backend (ADR-012)."""

from __future__ import annotations

import http.server
import io
import json
import socket
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from genesis_worker.utils.acquire import GithubReleaseTarball
from genesis_worker.utils.install import InstallLayout, Manifest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_tarball(path: Path, files: dict[str, bytes]) -> Path:
    archive = path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for rel, content in files.items():
            info = tarfile.TarInfo(rel)
            info.size = len(content)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(content))
    return archive


class _FakeServer:
    """Routes are either fast or slow-streaming, on the same server."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._routes: dict[str, tuple[bytes, dict[str, str]]] = {}
        self._slow: dict[str, tuple[bytes, float]] = {}
        self._lock = threading.Lock()
        self._httpd = http.server.HTTPServer(("127.0.0.1", port), _make_handler(self))
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def route(self, path: str, body: bytes, *, content_type: str = "application/json") -> None:
        with self._lock:
            self._routes[path] = (body, {"Content-Type": content_type})

    def slow_route(self, path: str, body: bytes, *, chunk_delay_ms: int) -> None:
        with self._lock:
            self._slow[path] = (body, chunk_delay_ms / 1000.0)

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _make_handler(server: _FakeServer) -> type[http.server.BaseHTTPRequestHandler]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API)
            with server._lock:
                if self.path in server._routes:
                    body, headers = server._routes[self.path]
                    self.send_response(200)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path in server._slow:
                    body, delay = server._slow[self.path]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    i = 0
                    chunk = 4096
                    while i < len(body):
                        self.wfile.write(body[i : i + chunk])
                        self.wfile.flush()
                        i += chunk
                        if delay > 0:
                            time.sleep(delay)
                    return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: Any, **kwargs: Any) -> None:  # noqa: A002
            return

    return _Handler


@pytest.fixture
def fake_github() -> Any:
    port = _free_port()
    server = _FakeServer(port)
    yield server
    server.shutdown()


def _release_json(tag: str, asset_name: str, asset_url: str, size: int = 0) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "assets": [
            {"name": asset_name, "browser_download_url": asset_url, "size": size},
        ],
    }


def _asset_matcher(asset_url: str):
    def _match(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
        for a in assets:
            if a["browser_download_url"] == asset_url:
                return a
        return None

    return _match


def _backend(
    tmp_path: Path,
    *,
    asset_url: str,
    checksums_url_for: Any | None = None,
) -> GithubReleaseTarball:
    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    return GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=_asset_matcher(asset_url),
        binary_rel="bin/test-tool",
        checksums_url=checksums_url_for,
    )


def test_install_completes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer) -> None:
    archive = _make_tarball(
        tmp_path, {"bin/test-tool": b"#!/bin/sh\necho ok\n"}
    )
    asset_url = f"http://127.0.0.1:{fake_github.port}/asset.tar.gz"
    fake_github.route(
        "/repos/o/r/releases/latest",
        json.dumps(_release_json("v1.0.0", "asset.tar.gz", asset_url, archive.stat().st_size)).encode(),
    )
    fake_github.route(
        "/asset.tar.gz", archive.read_bytes(), content_type="application/octet-stream"
    )

    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{fake_github.port}")

    backend = _backend(tmp_path, asset_url=asset_url)
    session = backend.install()
    final = session.wait()

    assert final.kind == "complete", final
    layout = backend.layout
    assert (layout.installs_root / "v1.0.0" / "bin" / "test-tool").exists()
    assert layout.current_symlink.readlink() == Path("v1.0.0")

    manifest = Manifest.from_yaml(layout.manifest_path("v1.0.0"))
    assert manifest.name == "test-tool"
    assert manifest.version == "v1.0.0"
    assert manifest.verified is False  # no checksums route configured
    assert manifest.sha256 is None


def test_install_fails_on_sha_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    archive = _make_tarball(tmp_path, {"bin/test-tool": b"#!/bin/sh\necho ok\n"})
    real_sha = _sha256_hex(archive)
    fake_sha = "0" * 64
    assert fake_sha != real_sha

    asset_url = f"http://127.0.0.1:{fake_github.port}/asset.tar.gz"
    fake_github.route(
        "/repos/o/r/releases/latest",
        json.dumps(_release_json("v1.0.0", "asset.tar.gz", asset_url, archive.stat().st_size)).encode(),
    )
    fake_github.route(
        "/asset.tar.gz", archive.read_bytes(), content_type="application/octet-stream"
    )
    checksums_url = f"http://127.0.0.1:{fake_github.port}/checksums.txt"
    fake_github.route(
        "/checksums.txt",
        f"{fake_sha}  asset.tar.gz\n".encode(),
        content_type="text/plain",
    )

    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{fake_github.port}")

    backend = _backend(
        tmp_path,
        asset_url=asset_url,
        checksums_url_for=lambda _rel: checksums_url,
    )
    final = backend.install().wait()

    assert final.kind == "failed"
    assert "sha256" in (final.error or "").lower()

    # No install dir left behind.
    layout = backend.layout
    assert not (layout.installs_root / "v1.0.0").exists()
    assert not layout.current_symlink.exists()


def test_install_cancel_mid_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    """Cancel called mid-download; the cache file is unlinked; symlink unchanged."""
    big_body = b"\x00" * 64 * 1024  # 64 KB, streams in many chunks
    asset_url = f"http://127.0.0.1:{fake_github.port}/asset.tar.gz"
    fake_github.route(
        "/repos/o/r/releases/latest",
        json.dumps(_release_json("v1.0.0", "asset.tar.gz", asset_url, len(big_body))).encode(),
    )
    fake_github.slow_route("/asset.tar.gz", big_body, chunk_delay_ms=20)

    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{fake_github.port}")

    backend = _backend(tmp_path, asset_url=asset_url)
    session = backend.install()
    time.sleep(0.05)
    session.cancel()
    final = session.wait()

    assert final.kind == "cancelled"
    layout = backend.layout
    assert not (layout.installs_root / "v1.0.0").exists()
    assert not layout.current_symlink.exists()


def _sha256_hex(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(64 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Upstream asset-name matchers — runs against the real repo's release payload
# ---------------------------------------------------------------------------


from genesis_worker.services.llama_swap.installs import (  # noqa: E402
    _ai_dock_llama_cpp_cuda_asset,
    _asset_name_matches_linux_amd64_tarball,
    _find_asset_by_suffix,
)


def test_linux_amd64_matcher_matches_goreleaser_convention() -> None:
    """mostlygeek/llama-swap ships ``..._linux_amd64.tar.gz`` for x86_64 Linux."""
    asset = {"name": "llama-swap_249_linux_amd64.tar.gz"}
    assert _asset_name_matches_linux_amd64_tarball(asset) is True


def test_linux_amd64_matcher_ignores_other_platforms() -> None:
    """Darwin, ARM64, FreeBSD, and Windows assets are not matches."""
    for name in (
        "llama-swap_249_darwin_amd64.tar.gz",
        "llama-swap_249_darwin_arm64.tar.gz",
        "llama-swap_249_freebsd_amd64.tar.gz",
        "llama-swap_249_linux_arm64.tar.gz",
        "llama-swap_249_windows_amd64.zip",
        "llama-swap_249_checksums.txt",
    ):
        assert _asset_name_matches_linux_amd64_tarball({"name": name}) is False, name


def test_llama_cpp_cuda_matcher_matches_actual_upstream() -> None:
    """ai-dock/llama.cpp-cuda publishes ``llama.cpp-b<build>-cuda-<ver>-amd64.tar.gz``."""
    asset = {"name": "llama.cpp-b10375-cuda-12.8-amd64.tar.gz"}
    assert _ai_dock_llama_cpp_cuda_asset(asset) is True


def test_llama_cpp_cuda_matcher_matches_semver_naming() -> None:
    """ai-dock switched to semver tags; the matcher must accept ``v<MAJOR>.<MINOR>.<PATCH>``."""
    asset = {"name": "llama.cpp-v0.3.0-cuda-12.8-amd64.tar.gz"}
    assert _ai_dock_llama_cpp_cuda_asset(asset) is True


def test_llama_cpp_cuda_matcher_rejects_other_platforms() -> None:
    for name in (
        "llama.cpp-b10375-cuda-12.8-arm64.tar.gz",
        "llama.cpp-b10375-cuda-12.8.tar.gz",  # missing arch
        "llama.cpp-cuda-12.8-amd64.tar.gz",  # old/incorrect prefix
    ):
        assert _ai_dock_llama_cpp_cuda_asset({"name": name}) is False, name


def test_find_asset_by_suffix_resolves_checksums() -> None:
    """The checksums asset is selected by suffix to wire ``checksums_url``."""
    release_assets = [
        {"name": "llama-swap_249_darwin_amd64.tar.gz", "browser_download_url": "u1"},
        {"name": "llama-swap_249_linux_amd64.tar.gz", "browser_download_url": "u2"},
        {"name": "llama-swap_249_checksums.txt", "browser_download_url": "u3"},
    ]
    assert _find_asset_by_suffix(release_assets, "_checksums.txt") == release_assets[2]


# ---------------------------------------------------------------------------
# available_versions — multi-release tracking
# ---------------------------------------------------------------------------


def test_available_versions_lists_multiple_releases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    """``/releases`` returns a list; available_versions projects each into InstallVersion."""
    port = fake_github.port
    url_v045 = f"http://127.0.0.1:{port}/asset-v0.4.5.tar.gz"
    url_v044 = f"http://127.0.0.1:{port}/asset-v0.4.4.tar.gz"
    rels = [
        _release_json("v0.4.5", "asset.tar.gz", url_v045, 1000),
        _release_json("v0.4.4", "asset.tar.gz", url_v044, 1000),
    ]
    fake_github.route(
        "/repos/o/r/releases?per_page=50",
        json.dumps(rels).encode(),
    )

    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")

    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    backend = GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=lambda assets: next(
            (a for a in assets if a.get("browser_download_url", "").startswith(f"http://127.0.0.1:{port}/")),
            None,
        ),
        binary_rel="bin/test-tool",
    )

    versions = backend.available_versions()

    assert [v.version for v in versions] == ["v0.4.5", "v0.4.4"]
    assert [v.url for v in versions] == [url_v045, url_v044]


def test_install_with_specific_version_uses_tag_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    """A user-picked version fetches /releases/tags/<version>, not /releases/latest."""
    archive = _make_tarball(tmp_path, {"bin/test-tool": b"#!/bin/sh\necho ok\n"})
    asset_url = f"http://127.0.0.1:{fake_github.port}/asset.tar.gz"
    fake_github.route(
        "/repos/o/r/releases/tags/v0.4.4",
        json.dumps(_release_json("v0.4.4", "asset.tar.gz", asset_url, archive.stat().st_size)).encode(),
    )
    fake_github.route(
        "/asset.tar.gz", archive.read_bytes(), content_type="application/octet-stream"
    )

    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{fake_github.port}")

    backend = _backend(tmp_path, asset_url=asset_url)
    final = backend.install(version="v0.4.4").wait()

    assert final.kind == "complete"
    assert (backend.layout.installs_root / "v0.4.4" / "bin" / "test-tool").exists()
    assert backend.layout.current_symlink.readlink() == Path("v0.4.4")


# ---------------------------------------------------------------------------
# Release cache — reduces GitHub rate-limit pressure
# ---------------------------------------------------------------------------


def test_available_versions_caches_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    """First call writes a JSON cache file under cache_root."""
    port = fake_github.port
    fake_github.route(
        "/repos/o/r/releases?per_page=50",
        json.dumps(
            [_release_json("v0.4.5", "asset.tar.gz", f"http://127.0.0.1:{port}/asset.tar.gz", 100)]
        ).encode(),
    )
    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")

    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    backend = GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=lambda assets: next(
            (a for a in assets if a.get("browser_download_url", "").startswith(f"http://127.0.0.1:{port}/")),
            None,
        ),
        binary_rel="bin/test-tool",
    )

    assert backend.available_versions()[0].version == "v0.4.5"

    cache_path = backend._release_cache_path()  # noqa: SLF001
    assert cache_path.is_file()
    payload = json.loads(cache_path.read_text())
    assert payload["version"] == 1
    assert isinstance(payload["fetched_at"], (int, float))
    assert isinstance(payload["releases"], list)
    assert payload["releases"][0]["tag_name"] == "v0.4.5"


def test_release_cache_hits_within_ttl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    """After a successful fetch, available_versions() does not call _http_get_json again.

    We instrument by counting how many times the next URL is queried. A single
    request on the first call, zero on the second, is the desired pattern.
    """
    port = fake_github.port

    counter = {"hits": 0}
    import urllib.request as _ur

    real_urlopen = _ur.urlopen

    def counting_urlopen(req_or_url, *args, **kwargs):  # type: ignore[no-untyped-def]
        url = getattr(req_or_url, "full_url", req_or_url)
        if isinstance(url, str) and "releases?per_page=50" in url:
            counter["hits"] += 1
        return real_urlopen(req_or_url, *args, **kwargs)

    monkeypatch.setattr(_ur, "urlopen", counting_urlopen)

    fake_github.route(
        "/repos/o/r/releases?per_page=50",
        json.dumps(
            [_release_json("v0.4.5", "asset.tar.gz", f"http://127.0.0.1:{port}/asset.tar.gz", 100)]
        ).encode(),
    )
    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")

    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    backend = GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=lambda assets: next(
            (a for a in assets if a.get("browser_download_url", "").startswith(f"http://127.0.0.1:{port}/")),
            None,
        ),
        binary_rel="bin/test-tool",
        release_cache_ttl_s=900,
    )

    backend.available_versions()
    assert counter["hits"] == 1, "first call should issue one request"
    backend.available_versions()
    backend.available_versions()
    assert counter["hits"] == 1, "subsequent calls within TTL must hit the cache"


def test_release_cache_ttl_expiry_triggers_refetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    """Cache older than TTL is ignored; available_versions() refetches."""
    port = fake_github.port
    fake_github.route(
        "/repos/o/r/releases?per_page=50",
        json.dumps(
            [_release_json("v0.5.0", "asset.tar.gz", f"http://127.0.0.1:{port}/asset.tar.gz", 100)]
        ).encode(),
    )
    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")

    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    backend = GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=lambda assets: next(
            (a for a in assets if a.get("browser_download_url", "").startswith(f"http://127.0.0.1:{port}/")),
            None,
        ),
        binary_rel="bin/test-tool",
        release_cache_ttl_s=60,
    )

    # Seed a stale cache.
    cache_path = backend._release_cache_path()  # noqa: SLF001
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"version": 1, "fetched_at": 1.0, "releases": []})
    )

    versions = backend.available_versions()
    # The fresh fetch returned v0.5.0; the cache file should now reflect it.
    assert versions[0].version == "v0.5.0"
    payload = json.loads(cache_path.read_text())
    assert payload["releases"][0]["tag_name"] == "v0.5.0"


def test_invalidate_release_cache_removes_cache_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_github: _FakeServer
) -> None:
    port = fake_github.port
    fake_github.route(
        "/repos/o/r/releases?per_page=50",
        json.dumps(
            [_release_json("v1", "asset.tar.gz", f"http://127.0.0.1:{port}/asset.tar.gz", 1)]
        ).encode(),
    )
    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")

    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    backend = GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=lambda assets: next(
            (a for a in assets if a.get("browser_download_url", "").startswith(f"http://127.0.0.1:{port}/")),
            None,
        ),
        binary_rel="bin/test-tool",
    )

    backend.available_versions()
    cache_path = backend._release_cache_path()  # noqa: SLF001
    assert cache_path.is_file()

    backend.invalidate_release_cache()
    assert not cache_path.exists()

    backend.available_versions()
    assert cache_path.is_file()


# ---------------------------------------------------------------------------
# Secrets accessor — framework-managed contract (ADR-012)
# ---------------------------------------------------------------------------


from genesis_worker.contracts import (  # noqa: E402
    NoSecretsAccessor,
    StaticSecretsAccessor,
)


def test_no_secrets_accessor_returns_none() -> None:
    """The default accessor used by tests returns None for any key."""
    assert NoSecretsAccessor().get("github_token") is None


def test_static_secrets_accessor_returns_static_value() -> None:
    a = StaticSecretsAccessor({"github_token": "abc"})
    assert a.get("github_token") == "abc"
    assert a.get("missing") is None


def test_backend_with_secrets_attaches_bearer(
    monkeypatch: pytest.MonkeyPatch, fake_github: _FakeServer, tmp_path: Path
) -> None:
    """When the backend has a secrets accessor resolving github_token, Authorization header is sent."""
    import io
    import urllib.request as _ur

    port = fake_github.port
    fake_github.route(
        "/repos/o/r/releases?per_page=50",
        json.dumps(
            [_release_json("v1", "asset.tar.gz", f"http://127.0.0.1:{port}/asset.tar.gz", 1)]
        ).encode(),
    )
    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")

    captured: dict[str, Any] = {}

    def fake_urlopen(req_or_url: Any, *args: Any, **kwargs: Any) -> io.BytesIO:
        if isinstance(req_or_url, _ur.Request):
            captured["headers"] = dict(req_or_url.headers)
        return io.BytesIO(b"[]")

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)

    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    backend = GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=lambda assets: next(
            (a for a in assets if a.get("browser_download_url", "").startswith(f"http://127.0.0.1:{port}/")),
            None,
        ),
        binary_rel="bin/test-tool",
        secrets=StaticSecretsAccessor({"github_token": "test_pat_xyz"}),
    )
    backend.available_versions()  # cache miss → fetch

    assert captured["headers"].get("Authorization") == "Bearer test_pat_xyz"


def test_backend_without_secrets_omits_authorization(
    monkeypatch: pytest.MonkeyPatch, fake_github: _FakeServer, tmp_path: Path
) -> None:
    """Without a secrets accessor, no Authorization header is sent."""
    import io
    import urllib.request as _ur

    port = fake_github.port
    fake_github.route(
        "/repos/o/r/releases?per_page=50",
        json.dumps(
            [_release_json("v1", "asset.tar.gz", f"http://127.0.0.1:{port}/asset.tar.gz", 1)]
        ).encode(),
    )
    monkeypatch.setenv("GENESIS_INSTALL_GITHUB_API", f"http://127.0.0.1:{port}")

    captured: dict[str, Any] = {}

    def fake_urlopen(req_or_url: Any, *args: Any, **kwargs: Any) -> io.BytesIO:
        if isinstance(req_or_url, _ur.Request):
            captured["headers"] = dict(req_or_url.headers)
        return io.BytesIO(b"[]")

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)

    layout = InstallLayout(tmp_path / "data", tmp_path / "state", "test-tool")
    backend = GithubReleaseTarball(
        name="test-tool",
        repo_owner="o",
        repo_name="r",
        layout=layout,
        cache_root=tmp_path / "cache",
        asset_for=lambda assets: next(
            (a for a in assets if a.get("browser_download_url", "").startswith(f"http://127.0.0.1:{port}/")),
            None,
        ),
        binary_rel="bin/test-tool",
    )
    backend.available_versions()

    assert "Authorization" not in captured["headers"]


# ---------------------------------------------------------------------------
# Settings — framework-managed secrets (ADR-012)
# ---------------------------------------------------------------------------


def test_settings_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings.secrets.github_token reads GENESIS_SECRETS__GITHUB_TOKEN."""
    monkeypatch.setenv("GENESIS_SECRETS__GITHUB_TOKEN", "env_token_xyz")
    from genesis_worker.settings import Settings

    s = Settings()
    assert s.secrets.github_token == "env_token_xyz"
    assert s.secret("github_token") == "env_token_xyz"


def test_settings_secret_from_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Settings falls back to the repo-root ``.env`` when env var is unset."""
    monkeypatch.delenv("GENESIS_SECRETS__GITHUB_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GENESIS_SECRETS__GITHUB_TOKEN=dotenv_token_xyz\n")

    monkeypatch.chdir(tmp_path)
    from genesis_worker.settings import Settings

    assert Settings().secrets.github_token == "dotenv_token_xyz"


def test_settings_accessor_returns_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from genesis_worker.settings import Settings

    monkeypatch.setenv("GENESIS_SECRETS__GITHUB_TOKEN", "accessor_token")
    accessor = Settings().secrets.accessor()
    assert accessor.get("github_token") == "accessor_token"
    assert accessor.get("missing") is None


def test_settings_no_secret_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GENESIS_SECRETS__GITHUB_TOKEN", raising=False)
    # Move CWD to a directory with no ``.env`` so the dotenv fallback
    # doesn't pick up a token from the worktree.
    monkeypatch.chdir(tmp_path)
    from genesis_worker.settings import Settings

    assert Settings().secrets.github_token is None
    assert Settings().secret("github_token") is None


def test_settings_accessor_serves_to_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    """GenesisWorker exposes secrets via .secret(name) shortcut."""
    from genesis_worker import GenesisWorker
    from genesis_worker.settings import Settings

    monkeypatch.setenv("GENESIS_SECRETS__GITHUB_TOKEN", "facade_token")
    worker = GenesisWorker(Settings())
    assert worker.secret("github_token") == "facade_token"
    assert worker.secrets.get("github_token") == "facade_token"
