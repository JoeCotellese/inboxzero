"""Tests for the daemon: scheduler setup, PID lifecycle, signal handling."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from mailfiler.daemon import PIDFile, create_scheduler, stop_daemon

if TYPE_CHECKING:
    from pathlib import Path


class TestPIDFile:
    def test_write_and_read(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        pid_file = PIDFile(pid_path)
        pid_file.write(12345)
        assert pid_file.read() == 12345

    def test_read_nonexistent_returns_none(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        pid_file = PIDFile(pid_path)
        assert pid_file.read() is None

    def test_remove(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        pid_file = PIDFile(pid_path)
        pid_file.write(12345)
        pid_file.remove()
        assert not pid_path.exists()

    def test_remove_nonexistent_is_safe(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        pid_file = PIDFile(pid_path)
        pid_file.remove()  # should not raise

    def test_context_manager(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        pid_file = PIDFile(pid_path)
        with pid_file:
            assert pid_path.exists()
            assert pid_file.read() == os.getpid()
        assert not pid_path.exists()


class TestCreateScheduler:
    def test_creates_scheduler(self) -> None:
        callback = MagicMock()
        scheduler = create_scheduler(callback, interval_minutes=5)
        assert scheduler is not None
        # Should have one job configured
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1


class TestStopDaemon:
    def test_stop_when_not_running(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        result = stop_daemon(pid_path)
        assert result is False

    def test_stop_with_stale_pid(self, tmp_path: Path) -> None:
        """Stop should handle a PID file for a process that no longer exists."""
        pid_path = tmp_path / "test.pid"
        pid_file = PIDFile(pid_path)
        pid_file.write(99999999)  # unlikely to be a real PID
        stop_daemon(pid_path)
        # Should clean up stale PID
        assert not pid_path.exists()
