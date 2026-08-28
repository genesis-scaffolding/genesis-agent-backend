"""Tests for ``ComfyUiOptions`` — defaults, overrides, PUID auto-default."""

from __future__ import annotations

from genesis_worker.services.comfyui.options import ComfyUiOptions


def test_default_image_repo_and_tag() -> None:
    opts = ComfyUiOptions()
    assert opts.image_repo == "ghcr.io/genesis-scaffolding/comfyui-cuda"
    assert opts.image_tag == "v0.34.0-cuda-13.0-amd64"


def test_default_listen_port_matches_comfyui() -> None:
    """ComfyUI's default port is 8188; we mirror it."""
    assert ComfyUiOptions().listen_port == 8188


def test_default_gpu_required_and_nvidia_runtime() -> None:
    opts = ComfyUiOptions()
    assert opts.gpu_required is True
    assert opts.runtime == "nvidia"
    assert opts.gpu_driver == "nvidia"
    assert opts.gpu_count == "1"


def test_default_restart_policy_is_unless_stopped() -> None:
    """The compose's restart policy is preserved by default."""
    assert ComfyUiOptions().restart_policy == "unless-stopped"


def test_default_extra_args_mirrors_compose() -> None:
    """``--verbose`` is the compose's default extra arg."""
    assert ComfyUiOptions().extra_args == ["--verbose"]


def test_puid_pgid_default_to_none() -> None:
    """When unset, the service constructs them from ``os.getuid``/``os.getgid``."""
    opts = ComfyUiOptions()
    assert opts.puid is None
    assert opts.pgid is None


def test_overrides_apply() -> None:
    opts = ComfyUiOptions(
        listen_port=9999,
        image_tag="v0.99.0-cuda-13.0-amd64",
        gpu_required=False,
        restart_policy="no",
    )
    assert opts.listen_port == 9999
    assert opts.image_tag == "v0.99.0-cuda-13.0-amd64"
    assert opts.gpu_required is False
    assert opts.restart_policy == "no"


def test_bind_mount_paths_default_to_none() -> None:
    """Defaults are derived in the service constructor from ``ctx``."""
    opts = ComfyUiOptions()
    assert opts.data_python_dir is None
    assert opts.data_custom_nodes_dir is None
    assert opts.data_input_dir is None
    assert opts.data_output_dir is None
    assert opts.data_profiles_dir is None
    assert opts.vault_models_dir is None


def test_symlinks_file_default_to_none() -> None:
    assert ComfyUiOptions().symlinks_file is None


def test_log_file_default_to_none() -> None:
    assert ComfyUiOptions().log_file is None
