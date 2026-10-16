"""Docker Compose stack lifecycle — up, down, ps, logs.

Wraps ``docker compose`` (built into Docker CE 23.0+). Each stack gets
a unique project directory under ``<state_dir>/compose/`` and a unique
project name (``<service-name>-stack``) so two compose services never
collide. All subprocess calls go through :func:`subprocess.run` with
``check=False, capture_output=True, text=True``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from genesis_worker.contracts import StartResult, StopResult

_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_PS_TIMEOUT_S = 10.0
# Image pulls can be large; the old ComposePullSession used 1800s per image.
_PULL_TIMEOUT_S = 1800.0


def _run(
    args: list[str], *, timeout: float, cwd: Path | None = None, env=None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, check=False, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env
    )


class DockerComposeStack:
    """Lifecycle for one docker compose stack identified by ``project_name``.

    ``project_dir`` is the working directory for every ``docker compose``
    invocation — it must contain the ``docker-compose.yml`` file that
    ``up`` writes before running the command.
    """

    def __init__(self, project_dir: Path, project_name: str) -> None:
        self._project_dir = project_dir
        self._project_name = project_name

    # --- lifecycle ---------------------------------------------------------

    def is_running(self) -> bool:
        """True iff at least one container in the project is in ``running`` state.

        A missing project dir (stack never started) means nothing is running.
        """
        if not self._project_dir.is_dir():
            return False
        result = _run(
            ["docker", "compose", "-p", self._project_name, "ps", "--format", "json"],
            timeout=_DEFAULT_PS_TIMEOUT_S,
            cwd=self._project_dir,
        )
        if result.returncode != 0:
            return False
        # ``docker compose ps --format json`` emits one JSON object per line.
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            state = entry.get("State", "").lower()
            if state == "running":
                return True
        return False

    def up(self, *, env: dict[str, str] | None = None) -> StartResult:
        """Start the compose stack: write compose file (caller's responsibility),
        then run ``docker compose up -d``.

        ``env`` is prepended to the subprocess environment so compose
        files can reference ``$ROMM_DB_HOST`` etc.
        """
        if not self._project_dir.is_dir():
            self._project_dir.mkdir(parents=True, exist_ok=True)
        compose_file = self._project_dir / "docker-compose.yml"
        if not compose_file.exists():
            return StartResult(
                ok=False,
                message=f"docker-compose.yml not found in {self._project_dir}; "
                "call render() before up()",
            )

        run_env = None
        if env:
            import os

            run_env = {**os.environ, **env}

        result = _run(
            ["docker", "compose", "-p", self._project_name, "up", "-d"],
            timeout=_DEFAULT_TIMEOUT_S,
            cwd=self._project_dir,
            env=run_env,
        )
        if result.returncode != 0:
            return StartResult(
                ok=False,
                message=f"docker compose up failed (rc={result.returncode}): "
                f"{(result.stderr or result.stdout or '').strip()[-500:]}",
            )
        return StartResult(ok=True, message=f"{self._project_name} started")

    def pull(self) -> StartResult:
        """``docker compose pull`` — fetch every image in the file at its
        rendered tag. Idempotent: present images are skipped. The compose
        file must exist (caller renders it first)."""
        if not self._project_dir.is_dir():
            self._project_dir.mkdir(parents=True, exist_ok=True)
        if not (self._project_dir / "docker-compose.yml").exists():
            return StartResult(
                ok=False,
                message=f"docker-compose.yml not found in {self._project_dir}; render before pull",
            )
        result = _run(
            ["docker", "compose", "-p", self._project_name, "pull"],
            timeout=_PULL_TIMEOUT_S,
            cwd=self._project_dir,
        )
        if result.returncode != 0:
            return StartResult(
                ok=False,
                message=f"docker compose pull failed (rc={result.returncode}): "
                f"{(result.stderr or result.stdout or '').strip()[-500:]}",
            )
        return StartResult(ok=True, message=f"{self._project_name} images pulled")

    def down(self) -> StopResult:
        """Stop and remove containers, networks (but not named volumes)."""
        result = _run(
            ["docker", "compose", "-p", self._project_name, "down"],
            timeout=_DEFAULT_TIMEOUT_S,
            cwd=self._project_dir,
        )
        # ``docker compose down`` exits 0 even when nothing is running.
        if result.returncode != 0:
            return StopResult(
                ok=False,
                message=f"docker compose down failed (rc={result.returncode}): "
                f"{(result.stderr or result.stdout or '').strip()[-500:]}",
            )
        return StopResult(ok=True, message=f"{self._project_name} stopped")

    def restart(self) -> StartResult:
        """``docker compose restart`` — stop then up without removing containers."""
        result = _run(
            ["docker", "compose", "-p", self._project_name, "restart"],
            timeout=_DEFAULT_TIMEOUT_S,
            cwd=self._project_dir,
        )
        if result.returncode != 0:
            return StartResult(
                ok=False,
                message=f"docker compose restart failed (rc={result.returncode}): "
                f"{(result.stderr or result.stdout or '').strip()[-500:]}",
            )
        return StartResult(ok=True, message=f"{self._project_name} restarted")

    def logs(self, tail_lines: int = 200) -> str:
        """Last ``tail_lines`` of combined stdout/stderr from all containers."""
        if not self._project_dir.is_dir():
            return ""
        result = _run(
            [
                "docker",
                "compose",
                "-p",
                self._project_name,
                "logs",
                "--tail",
                str(int(tail_lines)),
            ],
            timeout=_DEFAULT_PS_TIMEOUT_S,
            cwd=self._project_dir,
        )
        if result.returncode != 0:
            return ""
        return (result.stdout or "") + (result.stderr or "")


__all__ = ["DockerComposeStack"]
