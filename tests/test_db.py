"""Tests for database schema and queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailfiler.db.queries import (
    delete_sender_profile,
    get_domain_profile,
    get_processed_email_by_gmail_id,
    get_sender_profile,
    list_processed_emails,
    list_sender_profiles_for_domain,
    upsert_domain_profile,
    upsert_processed_email,
    upsert_sender_profile,
)
from mailfiler.db.schema import initialize_db

if TYPE_CHECKING:
    from pathlib import Path


class TestSchema:
    def test_creates_tables(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "domain_profiles" in tables
        assert "processed_emails" in tables
        assert "sender_profiles" in tables
        conn.close()

    def test_creates_indexes(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_sender_email" in indexes
        assert "idx_sender_domain" in indexes
        assert "idx_processed_gmail_id" in indexes
        assert "idx_processed_from" in indexes
        assert "idx_processed_at" in indexes
        conn.close()

    def test_idempotent_initialization(self, tmp_db_path: Path) -> None:
        """Running initialize_db twice should not error."""
        conn1 = initialize_db(tmp_db_path)
        conn1.close()
        conn2 = initialize_db(tmp_db_path)
        conn2.close()

    def test_wal_mode_enabled(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_foreign_keys_enabled(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()


class TestSenderProfileCRUD:
    def _make_sender(self, **overrides: object) -> dict[str, object]:
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

    def test_upsert_and_get(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        data = self._make_sender()
        upsert_sender_profile(conn, data)
        result = get_sender_profile(conn, "news@example.com")
        assert result is not None
        assert result["email"] == "news@example.com"
        assert result["domain"] == "example.com"
        assert result["category"] == "newsletter"
        assert result["confidence"] == 0.9
        conn.close()

    def test_upsert_updates_existing(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        data = self._make_sender()
        upsert_sender_profile(conn, data)
        data["confidence"] = 0.95
        data["seen_count"] = 5
        upsert_sender_profile(conn, data)
        result = get_sender_profile(conn, "news@example.com")
        assert result is not None
        assert result["confidence"] == 0.95
        assert result["seen_count"] == 5
        conn.close()

    def test_get_missing_returns_none(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        result = get_sender_profile(conn, "nobody@example.com")
        assert result is None
        conn.close()

    def test_delete_sender_profile(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        data = self._make_sender()
        upsert_sender_profile(conn, data)
        deleted = delete_sender_profile(conn, "news@example.com")
        assert deleted is True
        assert get_sender_profile(conn, "news@example.com") is None
        conn.close()

    def test_delete_nonexistent_returns_false(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        deleted = delete_sender_profile(conn, "nobody@example.com")
        assert deleted is False
        conn.close()

    def test_list_senders_for_domain(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, self._make_sender(email="a@example.com"))
        upsert_sender_profile(conn, self._make_sender(email="b@example.com"))
        upsert_sender_profile(conn, self._make_sender(email="c@other.com", domain="other.com"))
        results = list_sender_profiles_for_domain(conn, "example.com")
        assert len(results) == 2
        emails = {r["email"] for r in results}
        assert emails == {"a@example.com", "b@example.com"}
        conn.close()

    def test_unique_email_constraint(self, tmp_db_path: Path) -> None:
        """Upserting same email should update, not duplicate."""
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, self._make_sender())
        upsert_sender_profile(conn, self._make_sender(confidence=0.5))
        count = conn.execute("SELECT COUNT(*) FROM sender_profiles").fetchone()[0]
        assert count == 1
        conn.close()


class TestDomainProfileCRUD:
    def _make_domain(self, **overrides: object) -> dict[str, object]:
        defaults: dict[str, object] = {
            "domain": "example.com",
            "category": "newsletter",
            "action": "archive",
            "label": "mailfiler/newsletter",
            "confidence": 0.9,
            "source": "promoted",
            "seen_count": 10,
            "sender_count": 3,
            "last_seen": "2026-03-16T10:00:00Z",
            "first_seen": "2026-03-01T10:00:00Z",
            "user_pinned": False,
        }
        defaults.update(overrides)
        return defaults

    def test_upsert_and_get(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_domain_profile(conn, self._make_domain())
        result = get_domain_profile(conn, "example.com")
        assert result is not None
        assert result["domain"] == "example.com"
        assert result["category"] == "newsletter"
        assert result["confidence"] == 0.9
        conn.close()

    def test_upsert_updates_existing(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_domain_profile(conn, self._make_domain())
        upsert_domain_profile(conn, self._make_domain(confidence=0.95, sender_count=5))
        result = get_domain_profile(conn, "example.com")
        assert result is not None
        assert result["confidence"] == 0.95
        assert result["sender_count"] == 5
        conn.close()

    def test_get_missing_returns_none(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        result = get_domain_profile(conn, "nope.com")
        assert result is None
        conn.close()


class TestProcessedEmailCRUD:
    def _make_processed(self, **overrides: object) -> dict[str, object]:
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
            "decision_source": "cache:sender",
            "confidence": 0.92,
            "llm_category": None,
            "llm_reason": None,
            "was_overridden": False,
        }
        defaults.update(overrides)
        return defaults

    def test_insert_and_get(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_processed_email(conn, self._make_processed())
        result = get_processed_email_by_gmail_id(conn, "msg_001")
        assert result is not None
        assert result["gmail_message_id"] == "msg_001"
        assert result["action_taken"] == "archive"
        assert result["confidence"] == 0.92
        conn.close()

    def test_get_missing_returns_none(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        result = get_processed_email_by_gmail_id(conn, "nonexistent")
        assert result is None
        conn.close()

    def test_list_processed_emails_with_limit(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        for i in range(10):
            upsert_processed_email(
                conn,
                self._make_processed(
                    gmail_message_id=f"msg_{i:03d}",
                    processed_at=f"2026-03-16T{10 + i}:00:00Z",
                ),
            )
        results = list_processed_emails(conn, limit=5)
        assert len(results) == 5
        # Should be most recent first
        assert results[0]["gmail_message_id"] == "msg_009"
        conn.close()

    def test_upsert_updates_override_status(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_processed_email(conn, self._make_processed())
        upsert_processed_email(conn, self._make_processed(was_overridden=True))
        result = get_processed_email_by_gmail_id(conn, "msg_001")
        assert result is not None
        assert result["was_overridden"] == 1
        conn.close()

    def test_unique_gmail_id_constraint(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_processed_email(conn, self._make_processed())
        upsert_processed_email(conn, self._make_processed(action_taken="keep_inbox"))
        count = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
        assert count == 1
        conn.close()
