"""Shared test fixtures for mailfiler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Write a minimal valid config.toml and return its path."""
    config_text = """\
[gmail]
credentials_file = "~/.mailfiler/credentials.json"
token_file = "~/.mailfiler/token.json"
poll_interval_minutes = 5
max_emails_per_run = 50

[llm]
provider = "anthropic"
model = "claude-haiku-4-5"
max_tokens = 500
timeout_seconds = 10

[rules]
allow_trash = false
confidence_threshold = 0.85
llm_threshold = 0.6
confirmation_days = 7

[vip_senders]
emails = []

[vip_domains]
domains = []

[blocked_senders]
emails = []

[labels]
prefix = "mailfiler"

[database]
path = "{db_path}"

[daemon]
pid_file = "{pid_path}"
run_mode = "observe"
"""
    db_path = tmp_path / "mailfiler.db"
    pid_path = tmp_path / "mailfiler.pid"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        config_text.format(db_path=db_path, pid_path=pid_path)
    )
    return config_path


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Return a temporary database path."""
    return tmp_path / "test.db"
