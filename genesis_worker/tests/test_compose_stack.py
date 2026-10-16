"""Tests for DockerComposeStack utility."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis_worker.utils.services.compose_stack import DockerComposeStack


class TestDockerComposeStack:
    def test_is_running_false_when_no_compose_file(self, tmp_path: Path) -> None:
        """is_running() returns False when docker-compose.yml is absent."""
        stack = DockerComposeStack(tmp_path, "test-stack")
        # No mock needed — the compose file is missing so docker compose
        # will exit non-zero; is_running catches that.
        assert stack.is_running() is False

    def test_down_idempotent_when_no_compose_file(self, tmp_path: Path) -> None:
        """down() succeeds (rc=0) even when nothing is running."""
        stack = DockerComposeStack(tmp_path, "test-stack")
        result = stack.down()
        assert result.ok is True

    def test_up_fails_when_compose_file_missing(self, tmp_path: Path) -> None:
        """up() fails with a clear message when docker-compose.yml is absent."""
        stack = DockerComposeStack(tmp_path, "test-stack")
        result = stack.up()
        assert result.ok is False
        assert "docker-compose.yml" in result.message

    def test_logs_empty_when_no_compose_file(self, tmp_path: Path) -> None:
        """logs() returns empty string when docker-compose.yml is absent."""
        stack = DockerComposeStack(tmp_path, "test-stack")
        assert stack.logs() == ""

    def test_logs_returns_combined_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """logs() returns stdout + stderr from docker compose logs."""

        def fake_run(args, **kwargs):
            assert "logs" in args
            result = pytest.importorskip("subprocess").CompletedProcess(
                args=args,
                returncode=0,
                stdout="romm stdout line\n",
                stderr="romm-db stderr line\n",
            )
            return result

        monkeypatch.setattr("genesis_worker.utils.services.compose_stack._run", fake_run)
        stack = DockerComposeStack(tmp_path, "test-stack")
        logs = stack.logs(tail_lines=50)
        assert "stdout" in logs
        assert "stderr" in logs

    def test_is_running_false_when_project_dir_missing(self, tmp_path: Path) -> None:
        """A never-started stack (compose dir absent) is not running — no crash.

        Regression: subprocess.run(cwd=<missing>) raises FileNotFoundError.
        """
        missing = tmp_path / "does-not-exist" / "compose"
        stack = DockerComposeStack(missing, "never-started")
        assert stack.is_running() is False
        assert stack.logs() == ""

    def test_is_running_true_when_container_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """is_running() returns True when at least one container reports State=running."""
        import subprocess

        def fake_run(args, **kwargs):
            assert "ps" in args
            assert "--format" in args
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"Name":"romm","State":"running"}\n{"Name":"romm-db","State":"running"}\n',
                stderr="",
            )

        monkeypatch.setattr("genesis_worker.utils.services.compose_stack._run", fake_run)
        stack = DockerComposeStack(tmp_path, "test-stack")
        assert stack.is_running() is True

    def test_is_running_false_when_all_stopped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """is_running() returns False when all containers are exited."""
        import subprocess

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"Name":"romm","State":"exited"}\n',
                stderr="",
            )

        monkeypatch.setattr("genesis_worker.utils.services.compose_stack._run", fake_run)
        stack = DockerComposeStack(tmp_path, "test-stack")
        assert stack.is_running() is False

    def test_up_writes_compose_file_and_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """up() requires docker-compose.yml to exist before running."""
        import subprocess

        called_args: list = []

        def fake_run(args, **kwargs):
            called_args.append(args)
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="Container romm  Running\n",
                stderr="",
            )

        monkeypatch.setattr("genesis_worker.utils.services.compose_stack._run", fake_run)
        stack = DockerComposeStack(tmp_path, "test-stack")
        # Missing compose file → fails
        result = stack.up()
        assert result.ok is False

        # Write compose file → succeeds
        (tmp_path / "docker-compose.yml").write_text(
            "version: '3.8'\nservices:\n  test: {image: alpine}\n"
        )
        result = stack.up()
        assert result.ok is True
        assert any("compose" in a and "up" in a for a in called_args)
