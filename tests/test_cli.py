"""Tests for the CLI interface."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

from mailfiler.cli import cli
from mailfiler.db.queries import get_sender_profile, upsert_processed_email, upsert_sender_profile
from mailfiler.db.schema import initialize_db

if TYPE_CHECKING:
    from pathlib import Path


def _write_config(tmp_path: Path) -> Path:
    """Write a minimal config file and return its path."""
    db_path = tmp_path / "mailfiler.db"
    pid_path = tmp_path / "mailfiler.pid"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f"""\
[gmail]
credentials_file = "x"
token_file = "x"

[llm]
provider = "anthropic"
model = "claude-haiku-4-5"

[rules]

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
""")
    return config_path


def _seed_db(tmp_path: Path) -> Path:
    """Create a DB with some test data and return the db path."""
    db_path = tmp_path / "mailfiler.db"
    conn = initialize_db(db_path)
    upsert_sender_profile(conn, {
        "email": "news@example.com",
        "domain": "example.com",
        "display_name": "Example News",
        "category": "newsletter",
        "action": "archive",
        "label": "mailfiler/newsletter",
        "confidence": 0.9,
        "source": "heuristic",
        "has_list_unsub": True,
        "has_precedence": "bulk",
        "dkim_valid": True,
        "spf_pass": True,
        "esp_fingerprint": None,
        "seen_count": 5,
        "correct_count": 3,
        "override_count": 0,
        "last_seen": "2026-03-16T10:00:00Z",
        "first_seen": "2026-03-01T10:00:00Z",
        "user_pinned": False,
        "notes": None,
    })
    upsert_processed_email(conn, {
        "gmail_message_id": "msg_001",
        "gmail_thread_id": "thread_001",
        "from_email": "news@example.com",
        "from_domain": "example.com",
        "subject": "Weekly Digest",
        "received_at": "2026-03-16T09:00:00Z",
        "processed_at": "2026-03-16T10:00:00Z",
        "action_taken": "archive",
        "label_applied": "mailfiler/newsletter",
        "decision_source": "cache:sender",
        "confidence": 0.92,
        "llm_category": None,
        "llm_reason": None,
        "was_overridden": False,
    })
    conn.close()
    return db_path


class TestCLIAudit:
    def test_audit_shows_processed_emails(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(config_path), "audit"])
        assert result.exit_code == 0
        assert "news@example.com" in result.output
        assert "archive" in result.output

    def test_audit_with_limit(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(config_path), "audit", "--n", "1"])
        assert result.exit_code == 0


class TestCLIPin:
    def test_pin_sender(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(config_path), "pin", "news@example.com"])
        assert result.exit_code == 0
        conn = initialize_db(db_path)
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["user_pinned"] == 1
        conn.close()

    def test_unpin_sender(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = _seed_db(tmp_path)
        # First pin
        runner = CliRunner()
        runner.invoke(cli, ["--config", str(config_path), "pin", "news@example.com"])
        # Then unpin
        result = runner.invoke(cli, ["--config", str(config_path), "unpin", "news@example.com"])
        assert result.exit_code == 0
        conn = initialize_db(db_path)
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["user_pinned"] == 0
        conn.close()


class TestCLITrust:
    def test_trust_sender(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(config_path), "trust", "news@example.com"])
        assert result.exit_code == 0
        conn = initialize_db(db_path)
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["action"] == "keep_inbox"
        assert profile["confidence"] == 1.0
        conn.close()


class TestCLIBlock:
    def test_block_sender(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(config_path), "block", "news@example.com"])
        assert result.exit_code == 0
        conn = initialize_db(db_path)
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["action"] == "archive"
        assert profile["confidence"] == 1.0
        conn.close()


class TestCLIResetSender:
    def test_reset_sender(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--config", str(config_path), "reset-sender", "news@example.com",
        ])
        assert result.exit_code == 0
        conn = initialize_db(db_path)
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is None
        conn.close()


class TestCLIStats:
    def test_stats_runs(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(config_path), "stats"])
        assert result.exit_code == 0


class TestCLIStatus:
    def test_status_shows_not_running(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        _seed_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(config_path), "status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower() or "status" in result.output.lower()
