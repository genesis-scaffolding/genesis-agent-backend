"""Docker-container lifecycle for the ComfyUI service.

Thin wrappers over :class:`DockerContainer` + :class:`HealthProbe`.
No tmux. Each function takes already-resolved values; the service
constructs them from options + ctx.
"""

from __future__ import annotations

from pathlib import Path

from ...contracts import ServiceState, ServiceStatus, StartResult, StopResult
from ...utils.net import HealthProbe
from ...utils.process import DockerContainer

_DEFAULT_HEALTH_PROBE_PATH = "/"


def start_comfyui(
    *,
    image: str,
    image_present: bool,
    container_name: str,
    listen_host: str,
    listen_port: int,
    volumes: dict[str, str],
    env: dict[str, str],
    runtime: str | None,
    gpu_flags: list[str] | None,
    extra_args: list[str] | None,
    restart_policy: str,
    hostname: str,
    vault_models_dir: Path,
    media_input_dir: Path,
    media_output_dir: Path,
) -> StartResult:
    """Create and start the ComfyUI container.

    ``image_present`` gates the run: callers should pre-check via
    :meth:`DockerContainer.image_present` so the user gets a clear
    "image not pulled" message rather than a docker-side error.
    ``vault_models_dir`` is the host directory ComfyUI is told to read
    as ``--models-directory /vault/comfyui``; we ``mkdir`` it here so
    a fresh install (no symlinks yet) still launches.
    ``media_input_dir`` and ``media_output_dir`` are the host dirs
    bind-mounted into ComfyUI's input/output (ADR-037); we ``mkdir``
    them too so docker doesn't initialise them as root.
    """
    if not image_present:
        return StartResult(ok=False, message=f"image not pulled: {image}")

    # Idempotent: the bind mount above only guarantees the vault root is
    # visible inside the container, not this specific subdir. ComfyUI's
    # CLI requires the models directory to exist at startup. The input
    # and output dirs (ADR-037) need the same treatment so docker
    # doesn't initialise them as root.
    vault_models_dir.mkdir(parents=True, exist_ok=True)
    for host_dir in (media_input_dir, media_output_dir):
        host_dir.mkdir(parents=True, exist_ok=True)

    container = DockerContainer(container_name)
    return container.run(
        image=image,
        command=extra_args,
        ports={f"{listen_port}/tcp": (listen_host, listen_port)},
        volumes=volumes,
        env=env,
        runtime=runtime,
        gpu_flags=gpu_flags,
        hostname=hostname,
        restart=restart_policy,
    )


def stop_comfyui(container_name: str, *, timeout_s: float = 30.0) -> StopResult:
    """Stop and remove the container (idempotent)."""
    container = DockerContainer(container_name)
    result = container.stop(timeout_s=timeout_s)
    container.remove()
    return result


def is_running_comfyui(container_name: str) -> bool:
    return DockerContainer(container_name).is_running()


def status_comfyui(
    container_name: str,
    listen_host: str,
    listen_port: int,
    *,
    health_probe_path: str = _DEFAULT_HEALTH_PROBE_PATH,
) -> ServiceStatus:
    """Coarse status: container presence + HTTP root probe."""
    endpoint = f"http://{HealthProbe.resolve_connect_host(listen_host)}:{listen_port}/"
    if not DockerContainer(container_name).is_running():
        return ServiceStatus(state=ServiceState.STOPPED, endpoint=endpoint)
    probe = HealthProbe(listen_host, listen_port, probe_path=health_probe_path)
    if probe.probe():
        return ServiceStatus(state=ServiceState.RUNNING, endpoint=endpoint)
    return ServiceStatus(state=ServiceState.STARTING, endpoint=endpoint)


def wait_ready_comfyui(
    listen_host: str,
    listen_port: int,
    timeout_s: float,
    *,
    health_probe_path: str = _DEFAULT_HEALTH_PROBE_PATH,
) -> bool:
    return HealthProbe(listen_host, listen_port, probe_path=health_probe_path).wait_ready(timeout_s)


def logs_comfyui(container_name: str, tail_lines: int) -> str:
    """Last ``tail_lines`` of the container's logs. Used by ``tail_log``."""
    return DockerContainer(container_name).logs(tail_lines=tail_lines)


__all__ = [
    "is_running_comfyui",
    "logs_comfyui",
    "start_comfyui",
    "status_comfyui",
    "stop_comfyui",
    "wait_ready_comfyui",
]
