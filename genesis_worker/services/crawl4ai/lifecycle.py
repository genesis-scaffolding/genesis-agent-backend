"""Docker-container lifecycle for the Crawl4AI service.

Thin wrappers over :class:`DockerContainer` + :class:`HealthProbe`.
No tmux. Each function takes already-resolved values; the service
constructs them from options + ctx.
"""

from __future__ import annotations

from ...contracts import ServiceState, ServiceStatus, StartResult, StopResult
from ...utils.net import HealthProbe
from ...utils.process import DockerContainer

# Crawl4AI's container listens here regardless of the host port we
# publish to (cf. the upstream ``11235:11235/tcp`` mapping).
_INTERNAL_PORT = 11235

# ``/`` requires the API token; ``/health`` is the loopback-friendly
# readiness endpoint that returns 200 without auth.
_DEFAULT_HEALTH_PROBE_PATH = "/health"


def start_crawl4ai(
    *,
    image: str,
    image_present: bool,
    container_name: str,
    listen_host: str,
    listen_port: int,
    volumes: dict[str, str],
    env: dict[str, str],
    restart_policy: str,
    hostname: str,
    shm_size: str | None,
    extra_args: list[str] | None,
) -> StartResult:
    """Create and start the Crawl4AI container.

    ``image_present`` gates the run: callers should pre-check via
    :meth:`DockerContainer.image_present` so the user gets a clear
    "image not pulled" message rather than a docker-side error.
    """
    if not image_present:
        return StartResult(ok=False, message=f"image not pulled: {image}")

    container = DockerContainer(container_name)
    return container.run(
        image=image,
        command=extra_args,
        ports={f"{_INTERNAL_PORT}/tcp": (listen_host, listen_port)},
        volumes=volumes,
        env=env,
        hostname=hostname,
        restart=restart_policy,
        shm_size=shm_size,
    )


def stop_crawl4ai(container_name: str, *, timeout_s: float = 30.0) -> StopResult:
    """Stop and remove the container (idempotent)."""
    container = DockerContainer(container_name)
    result = container.stop(timeout_s=timeout_s)
    container.remove()
    return result


def is_running_crawl4ai(container_name: str) -> bool:
    return DockerContainer(container_name).is_running()


def status_crawl4ai(
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


def wait_ready_crawl4ai(
    listen_host: str,
    listen_port: int,
    timeout_s: float,
    *,
    health_probe_path: str = _DEFAULT_HEALTH_PROBE_PATH,
) -> bool:
    return HealthProbe(listen_host, listen_port, probe_path=health_probe_path).wait_ready(timeout_s)


def logs_crawl4ai(container_name: str, tail_lines: int) -> str:
    """Last ``tail_lines`` of the container's logs. Used by ``tail_log``."""
    return DockerContainer(container_name).logs(tail_lines=tail_lines)


__all__ = [
    "is_running_crawl4ai",
    "logs_crawl4ai",
    "start_crawl4ai",
    "status_crawl4ai",
    "stop_crawl4ai",
    "wait_ready_crawl4ai",
]
