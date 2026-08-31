"""Tests for ``Crawl4AiOptions`` — defaults, overrides."""

from __future__ import annotations

from genesis_worker.services.crawl4ai.options import Crawl4AiOptions


def test_default_image_repo_and_tag() -> None:
    opts = Crawl4AiOptions()
    assert opts.image_repo == "unclecode/crawl4ai"
    assert opts.image_tag == "latest"


def test_default_listen_port_matches_upstream() -> None:
    """Crawl4AI's default port is 11235; we mirror it."""
    assert Crawl4AiOptions().listen_port == 11235


def test_default_listen_host_is_all_interfaces() -> None:
    assert Crawl4AiOptions().listen_host == "0.0.0.0"


def test_default_container_name() -> None:
    assert Crawl4AiOptions().container_name == "crawl4ai"


def test_default_restart_policy_is_unless_stopped() -> None:
    assert Crawl4AiOptions().restart_policy == "unless-stopped"


def test_puid_pgid_default_to_none() -> None:
    """When unset, the service constructs them from ``os.getuid``/``os.getgid``."""
    opts = Crawl4AiOptions()
    assert opts.puid is None
    assert opts.pgid is None


def test_extra_args_default_empty() -> None:
    assert Crawl4AiOptions().extra_args == []


def test_overrides_apply() -> None:
    opts = Crawl4AiOptions(
        listen_port=9999,
        image_tag="0.99.0",
        restart_policy="no",
    )
    assert opts.listen_port == 9999
    assert opts.image_tag == "0.99.0"
    assert opts.restart_policy == "no"


def test_data_path_default_to_none() -> None:
    """Default is derived in the service constructor from ``ctx``."""
    assert Crawl4AiOptions().data_path is None


def test_log_file_default_to_none() -> None:
    assert Crawl4AiOptions().log_file is None


def test_public_host_default_to_none() -> None:
    """When unset, the service falls back to ``socket.gethostname()``."""
    assert Crawl4AiOptions().public_host is None


def test_default_shm_size_is_oneg() -> None:
    """Crawl4AI runs Playwright/Chromium; the upstream docs recommend1g."""
    assert Crawl4AiOptions().shm_size == "1g"


def test_shm_size_override() -> None:
    opts = Crawl4AiOptions(shm_size="2g")
    assert opts.shm_size == "2g"


def test_default_api_token_is_none() -> None:
    """Auto-generated persistent token is the default path."""
    assert Crawl4AiOptions().api_token is None


def test_default_jwt_enabled_is_false() -> None:
    assert Crawl4AiOptions().jwt_enabled is False


def test_api_token_override() -> None:
    opts = Crawl4AiOptions(api_token="my-secret-token")
    assert opts.api_token == "my-secret-token"


def test_jwt_enabled_override() -> None:
    opts = Crawl4AiOptions(jwt_enabled=True)
    assert opts.jwt_enabled is True
