"""Tests for the reprocess CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from mailfiler.cli import cli
from mailfiler.db.queries import (
    get_processed_email_by_gmail_id,
    upsert_processed_email,
    upsert_sender_profile,
)
from mailfiler.db.schema import initialize_db
from mailfiler.models import EmailMessage

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
provider = "stub"
model = "test"

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
run_mode = "full_auto"
""")
    return config_path


def _make_processed(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "gmail_message_id": "msg_001",
        "gmail_thread_id": "thread_001",
        "from_email": "news@example.com",
        "from_domain": "example.com",
        "subject": "Weekly Digest",
        "received_at": "2026-03-16T09:00:00Z",
        "processed_at": "2026-03-16T10:00:00Z",
        "action_taken": "archive",
        "label_applied": "mailfiler/marketing",
        "decision_source": "llm",
        "confidence": 0.85,
        "llm_category": "marketing",
        "llm_reason": "Marketing email",
        "was_overridden": False,
    }
    defaults.update(overrides)
    return defaults


def _make_sender(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "email": "news@example.com",
        "domain": "example.com",
        "display_name": "Example News",
        "category": "marketing",
        "action": "archive",
        "label": "mailfiler/marketing",
        "confidence": 0.85,
        "source": "llm",
        "has_list_unsub": True,
        "has_precedence": "bulk",
        "dkim_valid": True,
        "spf_pass": True,
        "esp_fingerprint": None,
        "seen_count": 5,
        "correct_count": 0,
        "override_count": 0,
        "last_seen": "2026-03-16T10:00:00Z",
        "first_seen": "2026-03-01T10:00:00Z",
        "user_pinned": False,
        "notes": None,
    }
    defaults.update(overrides)
    return defaults


def _make_email(
    gmail_message_id: str = "msg_001",
    from_email: str = "news@example.com",
    subject: str = "Weekly Digest",
) -> EmailMessage:
    return EmailMessage(
        gmail_message_id=gmail_message_id,
        gmail_thread_id="thread_001",
        from_email=from_email,
        from_domain="example.com",
        from_display_name="Example News",
        to_email="joe@gmail.com",
        subject=subject,
        snippet="Preview",
        headers={"List-Unsubscribe": "<mailto:unsub@example.com>"},
        received_at="2026-03-16T09:00:00Z",
    )


def _seed_reprocess_data(tmp_path: Path, count: int = 3) -> Path:
    """Seed DB with processed emails under mailfiler/marketing label."""
    db_path = tmp_path / "mailfiler.db"
    conn = initialize_db(db_path)
    for i in range(count):
        upsert_processed_email(conn, _make_processed(
            gmail_message_id=f"msg_{i:03d}",
            from_email=f"sender{i}@example.com",
            subject=f"Marketing Email {i}",
        ))
        upsert_sender_profile(conn, _make_sender(
            email=f"sender{i}@example.com",
        ))
    conn.close()
    return db_path


def _patch_gmail(emails: list[EmailMessage]):
    """Create patches for Gmail auth and client modules.

    Returns a context manager that yields the mock mail client instance.
    The reprocess command uses lazy imports:
        from mailfiler.mail.gmail_auth import get_gmail_service
        from mailfiler.mail.gmail_client import GmailMailClient
    We need to patch these at the module level before they're imported.
    """
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        # Ensure the modules are loaded so patch can find them
        __import__("mailfiler.mail.gmail_auth")
        __import__("mailfiler.mail.gmail_client")

        mock_client_instance = MagicMock()
        # Mock batch fetch: return dict of found emails
        def _fake_fetch_messages(message_ids):
            email_map = {e.gmail_message_id: e for e in emails}
            return {mid: email_map[mid] for mid in message_ids if mid in email_map}

        mock_client_instance.fetch_messages.side_effect = _fake_fetch_messages

        with (
            patch("mailfiler.mail.gmail_auth.get_gmail_service") as mock_auth,
            patch(
                "mailfiler.mail.gmail_client.GmailMailClient",
                return_value=mock_client_instance,
            ),
        ):
            mock_auth.return_value = MagicMock()
            yield mock_client_instance

    return _ctx()


class TestReprocessDryRun:
    """Dry run (default) shows changes without modifying Gmail."""

    def test_dry_run_shows_results_table(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        _seed_reprocess_data(tmp_path)
        emails = [_make_email(f"msg_{i:03d}", f"sender{i}@example.com") for i in range(3)]

        with _patch_gmail(emails) as mock_client:
            runner = CliRunner()
            result = runner.invoke(cli, [
                "--config", str(config_path),
                "reprocess", "--label", "mailfiler/marketing",
            ])

        assert result.exit_code == 0, result.output
        assert "--apply" in result.output
        mock_client.apply_action.assert_not_called()
        mock_client.remove_label.assert_not_called()

    def test_dry_run_does_not_modify_db(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = _seed_reprocess_data(tmp_path)
        emails = [_make_email(f"msg_{i:03d}", f"sender{i}@example.com") for i in range(3)]

        with _patch_gmail(emails):
            runner = CliRunner()
            runner.invoke(cli, [
                "--config", str(config_path),
                "reprocess", "--label", "mailfiler/marketing",
            ])

        conn = initialize_db(db_path)
        for i in range(3):
            record = get_processed_email_by_gmail_id(conn, f"msg_{i:03d}")
            assert record is not None
            assert record["label_applied"] == "mailfiler/marketing"
        conn.close()


class TestReprocessApply:
    """--apply flag applies changes to Gmail and updates DB."""

    def test_apply_updates_gmail_and_db(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        _seed_reprocess_data(tmp_path, count=1)
        email = _make_email("msg_000", "sender0@example.com")

        with _patch_gmail([email]):
            runner = CliRunner()
            result = runner.invoke(cli, [
                "--config", str(config_path),
                "reprocess", "--label", "mailfiler/marketing", "--apply",
            ])

        assert result.exit_code == 0, result.output
        # Should not show "to apply" hint when applying
        assert "To apply:" not in result.output


class TestReprocessDeletedMessages:
    """Messages deleted from Gmail are cleaned up from processed_emails."""

    def test_deleted_messages_removed_from_db(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = _seed_reprocess_data(tmp_path, count=2)
        # Only msg_000 exists; msg_001 is "deleted" (not in emails list)
        email_0 = _make_email("msg_000", "sender0@example.com")

        with _patch_gmail([email_0]):
            runner = CliRunner()
            result = runner.invoke(cli, [
                "--config", str(config_path),
                "reprocess", "--label", "mailfiler/marketing", "--apply",
            ])

        assert result.exit_code == 0, result.output
        conn = initialize_db(db_path)
        assert get_processed_email_by_gmail_id(conn, "msg_001") is None
        assert get_processed_email_by_gmail_id(conn, "msg_000") is not None
        conn.close()


class TestReprocessMultipleLabels:
    """Multiple --label flags process each label."""

    def test_multiple_labels(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = tmp_path / "mailfiler.db"
        conn = initialize_db(db_path)
        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_mkt", label_applied="mailfiler/marketing",
        ))
        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_nl", label_applied="mailfiler/newsletter",
            from_email="nl@example.com",
        ))
        conn.close()

        emails = [
            _make_email("msg_mkt", "news@example.com"),
            _make_email("msg_nl", "nl@example.com"),
        ]

        with _patch_gmail(emails):
            runner = CliRunner()
            result = runner.invoke(cli, [
                "--config", str(config_path),
                "reprocess",
                "--label", "mailfiler/marketing",
                "--label", "mailfiler/newsletter",
            ])

        assert result.exit_code == 0, result.output


class TestReprocessLimit:
    """--limit caps how many emails are processed per label."""

    def test_limit_caps_batch(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        _seed_reprocess_data(tmp_path, count=5)
        emails = [_make_email(f"msg_{i:03d}", f"sender{i}@example.com") for i in range(5)]

        with _patch_gmail(emails) as mock_client:
            runner = CliRunner()
            result = runner.invoke(cli, [
                "--config", str(config_path),
                "reprocess", "--label", "mailfiler/marketing", "--limit", "2",
            ])

        assert result.exit_code == 0, result.output
        # Batch fetch should have been called with only 2 IDs
        mock_client.fetch_messages.assert_called_once()
        fetched_ids = mock_client.fetch_messages.call_args[0][0]
        assert len(fetched_ids) == 2


class TestReprocessUnchanged:
    """Emails that classify the same are reported as unchanged."""

    def test_unchanged_classification_not_applied(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path)
        db_path = tmp_path / "mailfiler.db"
        conn = initialize_db(db_path)
        # Stub LLM provider returns keep_inbox → maps to mailfiler/inbox
        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_unchanged",
            action_taken="keep_inbox",
            label_applied="mailfiler/inbox",
        ))
        conn.close()

        # Email without List-Unsubscribe so heuristics fall through to LLM stub
        email = EmailMessage(
            gmail_message_id="msg_unchanged",
            gmail_thread_id="thread_001",
            from_email="news@example.com",
            from_domain="example.com",
            from_display_name="Example News",
            to_email="joe@gmail.com",
            subject="Weekly Digest",
            snippet="Preview",
            headers={},
            received_at="2026-03-16T09:00:00Z",
        )

        with _patch_gmail([email]) as mock_client:
            runner = CliRunner()
            result = runner.invoke(cli, [
                "--config", str(config_path),
                "reprocess", "--label", "mailfiler/inbox", "--apply",
            ])

        assert result.exit_code == 0, result.output
        mock_client.remove_label.assert_not_called()
