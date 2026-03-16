"""Daemon process management: APScheduler polling, PID file, signal handlers."""

from __future__ import annotations

import logging
import os
import signal
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)


class PIDFile:
    """Manages a PID file for the daemon process."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, pid: int) -> None:
        """Write a PID to the file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(str(pid))

    def read(self) -> int | None:
        """Read the PID from the file, or None if it doesn't exist."""
        if not self._path.exists():
            return None
        try:
            return int(self._path.read_text().strip())
        except (ValueError, OSError):
            return None

    def remove(self) -> None:
        """Remove the PID file if it exists."""
        self._path.unlink(missing_ok=True)

    def __enter__(self) -> PIDFile:
        """Write current process PID on context entry."""
        self.write(os.getpid())
        return self

    def __exit__(self, *args: object) -> None:
        """Remove PID file on context exit."""
        self.remove()


def create_scheduler(
    callback: Callable[[], None],
    interval_minutes: int = 5,
) -> BackgroundScheduler:
    """Create an APScheduler BackgroundScheduler with the given callback.

    Args:
        callback: Function to call on each interval.
        interval_minutes: Polling interval in minutes.

    Returns:
        Configured (but not started) BackgroundScheduler.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_job(  # type: ignore[reportUnknownMemberType]  # apscheduler lacks stubs
        callback,
        "interval",
        minutes=interval_minutes,
        id="mailfiler_poll",
    )
    return scheduler


def stop_daemon(pid_path: Path) -> bool:
    """Stop the daemon by sending SIGTERM to the PID in the PID file.

    Args:
        pid_path: Path to the PID file.

    Returns:
        True if the daemon was stopped, False if it wasn't running.
    """
    pid_file = PIDFile(pid_path)
    pid = pid_file.read()

    if pid is None:
        logger.info("No PID file found — daemon not running")
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("Sent SIGTERM to PID %d", pid)
        pid_file.remove()
        return True
    except ProcessLookupError:
        logger.warning("PID %d not found — stale PID file, cleaning up", pid)
        pid_file.remove()
        return False
    except PermissionError:
        logger.error("Permission denied sending SIGTERM to PID %d", pid)
        return False
