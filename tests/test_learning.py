"""Tests for the implicit learning phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailfiler.config import AppConfig
from mailfiler.db.queries import (
    get_sender_profile,
    upsert_processed_email,
    upsert_sender_profile,
)
from mailfiler.db.schema import initialize_db
from mailfiler.pipeline.learning import LearningPhase
from tests.fakes import FakeMailClient

if TYPE_CHECKING:
    from pathlib import Path


def _default_config(**overrides: object) -> AppConfig:
    data: dict[str, object] = {
        "gmail": {"credentials_file": "x", "token_file": "x"},
        "llm": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "rules": {},
        "vip_senders": {"emails": []},
        "vip_domains": {"domains": []},
        "blocked_senders": {"emails": []},
        "labels": {"prefix": "mailfiler"},
        "database": {},
        "daemon": {},
    }
    data.update(overrides)
    return AppConfig.model_validate(data)


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
        "label_applied": "mailfiler/newsletter",
        "decision_source": "heuristic",
        "confidence": 0.92,
        "llm_category": None,
        "llm_reason": None,
        "was_overridden": False,
    }
    defaults.update(overrides)
    return defaults


def _make_sender(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
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
        "esp_fingerprint": "mailchimp",
        "seen_count": 1,
        "correct_count": 0,
        "override_count": 0,
        "last_seen": "2026-03-16T10:00:00Z",
        "first_seen": "2026-03-01T10:00:00Z",
        "user_pinned": False,
        "notes": None,
    }
    defaults.update(overrides)
    return defaults


class TestLearningPhase:
    """Tests for LearningPhase.learn()."""

    def test_archived_email_moved_to_inbox_pins_sender(self, tmp_db_path: Path) -> None:
        """If user moved an archived email back to INBOX, learn keep_inbox and pin."""
        conn = initialize_db(tmp_db_path)
        config = _default_config()
        mail_client = FakeMailClient()

        # Set up: email was archived, sender exists
        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_001",
            action_taken="archive",
            label_applied="mailfiler/newsletter",
            from_email="news@example.com",
        ))
        upsert_sender_profile(conn, _make_sender(
            email="news@example.com",
            action="archive",
            override_count=2,  # One more override will trigger pin
        ))

        # Gmail state: user moved it to inbox
        mail_client.set_message_labels("msg_001", ["INBOX", "mailfiler/newsletter"])

        phase = LearningPhase()
        corrections = phase.learn(conn, mail_client, config)

        assert len(corrections) == 1
        assert corrections[0].new_action == "keep_inbox"
        assert corrections[0].old_action == "archive"

        # Sender should be updated
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["action"] == "keep_inbox"

    def test_inbox_email_archived_updates_sender(self, tmp_db_path: Path) -> None:
        """If user archived an email that was kept in inbox, learn archive."""
        conn = initialize_db(tmp_db_path)
        config = _default_config()
        mail_client = FakeMailClient()

        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_002",
            action_taken="keep_inbox",
            label_applied=None,
            from_email="promo@store.com",
        ))
        upsert_sender_profile(conn, _make_sender(
            email="promo@store.com",
            domain="store.com",
            action="keep_inbox",
            label=None,
        ))

        # Gmail state: user archived it (no INBOX, no mailfiler/* label)
        mail_client.set_message_labels("msg_002", ["mailfiler/inbox"])

        phase = LearningPhase()
        corrections = phase.learn(conn, mail_client, config)

        assert len(corrections) == 1
        assert corrections[0].new_action == "archive"

    def test_label_changed_updates_sender_label(self, tmp_db_path: Path) -> None:
        """If user changed the mailfiler/* label, learn the new label."""
        conn = initialize_db(tmp_db_path)
        config = _default_config()
        mail_client = FakeMailClient()

        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_003",
            action_taken="archive",
            label_applied="mailfiler/newsletter",
            from_email="alerts@service.com",
        ))
        upsert_sender_profile(conn, _make_sender(
            email="alerts@service.com",
            domain="service.com",
            action="archive",
            label="mailfiler/newsletter",
        ))

        # Gmail state: user changed label to security
        mail_client.set_message_labels("msg_003", ["mailfiler/security"])

        phase = LearningPhase()
        corrections = phase.learn(conn, mail_client, config)

        assert len(corrections) == 1
        assert corrections[0].new_label == "mailfiler/security"
        assert corrections[0].old_label == "mailfiler/newsletter"

    def test_no_change_reconciled_without_correction(self, tmp_db_path: Path) -> None:
        """If labels match what we set, just reconcile with no correction."""
        conn = initialize_db(tmp_db_path)
        config = _default_config()
        mail_client = FakeMailClient()

        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_004",
            action_taken="archive",
            label_applied="mailfiler/newsletter",
            from_email="news@example.com",
        ))
        upsert_sender_profile(conn, _make_sender())

        # Gmail state: unchanged from what we set
        mail_client.set_message_labels("msg_004", ["mailfiler/newsletter"])

        phase = LearningPhase()
        corrections = phase.learn(conn, mail_client, config)

        assert len(corrections) == 0
        # But it should be reconciled
        from mailfiler.db.queries import get_processed_email_by_gmail_id
        result = get_processed_email_by_gmail_id(conn, "msg_004")
        assert result is not None
        assert result["reconciled_at"] is not None

    def test_gmail_api_error_skips_message(self, tmp_db_path: Path) -> None:
        """Gmail API error → message skipped (not reconciled)."""
        conn = initialize_db(tmp_db_path)
        config = _default_config()
        # Simulate an API error by using a client that raises.

        class ErrorMailClient(FakeMailClient):
            def get_message_labels(self, message_id: str) -> list[str]:
                raise RuntimeError("API timeout")

        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_005",
            from_email="news@example.com",
        ))

        phase = LearningPhase()
        corrections = phase.learn(conn, ErrorMailClient(), config)

        assert len(corrections) == 0
        # Not reconciled because of the error
        from mailfiler.db.queries import get_processed_email_by_gmail_id
        result = get_processed_email_by_gmail_id(conn, "msg_005")
        assert result is not None
        assert result["reconciled_at"] is None

    def test_no_sender_profile_no_crash(self, tmp_db_path: Path) -> None:
        """If sender has no profile, learning still works (just can't update profile)."""
        conn = initialize_db(tmp_db_path)
        config = _default_config()
        mail_client = FakeMailClient()

        upsert_processed_email(conn, _make_processed(
            gmail_message_id="msg_006",
            action_taken="archive",
            label_applied="mailfiler/newsletter",
            from_email="unknown@nowhere.com",
        ))
        # No sender profile for unknown@nowhere.com

        # Gmail state: user moved to inbox
        mail_client.set_message_labels("msg_006", ["INBOX", "mailfiler/newsletter"])

        phase = LearningPhase()
        corrections = phase.learn(conn, mail_client, config)

        # Correction detected even without sender profile
        assert len(corrections) == 1
        assert corrections[0].new_action == "keep_inbox"
