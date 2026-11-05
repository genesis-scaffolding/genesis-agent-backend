"""Radiance engine settings and container command rendering."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RADIANCE_INSTALLABLE_NAME = "radiance"


@dataclass(frozen=True)
class RadianceSettings:
    """Resolved radiance deployment settings for one model."""

    image_ref: str
    container_port: int = 8000
    gpu_index: int = 0
    group_names: tuple[str, ...] = ("render", "video")
    group_gids: tuple[tuple[str, str], ...] = ()
    tp: int = 1
    max_model_len: int = 32768
    max_num_seqs: int = 8
    kv_cache_dtype: str = "fp8"
    gpu_headroom_mib: int = 96
    ttl_s: int = 0
    unload_timeout_s: int = 90
    stop_grace_s: int = 60
    check_endpoint: str = "/v1/models"
    extra_docker_args: tuple[str, ...] = ()
    extra_flags: tuple[str, ...] = ()


def resolve_group_gids(names: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Resolve host group names to numeric GIDs, omitting groups that do not exist."""
    resolved: list[tuple[str, str]] = []
    for name in names:
        try:
            result = subprocess.run(
                ["getent", "group", name],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError):
            return ()
        if result.returncode != 0:
            continue
        fields = result.stdout.strip().split(":")
        if len(fields) < 3 or not fields[2].isdigit():
            continue
        resolved.append((name, fields[2]))
    return tuple(resolved)


def container_model_path(filename: str) -> str:
    """Return the in-container path for a model file."""
    return f"/models/{Path(filename).name}"


def render_radiance_cmd(
    settings: RadianceSettings,
    model_host_path: Path | str,
    model_container_path: Path | str,
) -> str:
    """Render the docker command that starts one radiance model."""
    host_path = str(model_host_path)
    container_path = str(model_container_path)
    mount = f"{host_path}:{container_path}:ro"

    sections = [
        "docker run --init --rm \\",
        "  --name ${MODEL_ID} \\",
        f"  -p ${{PORT}}:{settings.container_port} \\",
        "  --device /dev/kfd \\",
        "  --device /dev/dri \\",
    ]
    for _name, gid in settings.group_gids:
        sections.append(f"  --group-add {gid} \\")
    sections.extend(
        [
            "  --security-opt seccomp=unconfined \\",
            "  --ulimit memlock=-1:-1 \\",
            f"  -e ROCR_VISIBLE_DEVICES={settings.gpu_index} \\",
            f"  -v {shlex.quote(mount)} \\",
        ]
    )
    for arg in settings.extra_docker_args:
        sections.append(f"  {shlex.quote(arg)} \\")
    sections.append(f"  {shlex.quote(settings.image_ref)} \\")
    sections.append(f"  --model {shlex.quote(container_path)} \\")
    sections.append("  --host 0.0.0.0 \\")
    sections.append(f"  --port {settings.container_port} \\")
    sections.append(f"  --tp {settings.tp} \\")
    sections.append(f"  --max-num-seqs {settings.max_num_seqs} \\")
    sections.append(f"  --max-model-len {settings.max_model_len} \\")
    sections.append(f"  --kv-cache-dtype {settings.kv_cache_dtype} \\")
    gpu_suffix = " \\" if settings.extra_flags else ""
    sections.append(f"  --gpu-headroom-mib {settings.gpu_headroom_mib}{gpu_suffix}")
    for index, flag in enumerate(settings.extra_flags):
        suffix = " \\" if index < len(settings.extra_flags) - 1 else ""
        sections.append(f"  {flag}{suffix}")
    return "\n".join(sections)


__all__ = [
    "RADIANCE_INSTALLABLE_NAME",
    "RadianceSettings",
    "container_model_path",
    "render_radiance_cmd",
    "resolve_group_gids",
]
