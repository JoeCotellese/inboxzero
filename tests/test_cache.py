"""Tests for Layer 1: Sender/domain cache lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailfiler.db.queries import upsert_domain_profile, upsert_sender_profile
from mailfiler.db.schema import initialize_db
from mailfiler.models import Action, DecisionSource, EmailMessage
from mailfiler.pipeline.cache import CacheLayer

if TYPE_CHECKING:
    from pathlib import Path


def _make_email(
    from_email: str = "news@example.com",
    from_domain: str = "example.com",
) -> EmailMessage:
    return EmailMessage(
        gmail_message_id="msg_001",
        gmail_thread_id="thread_001",
        from_email=from_email,
        from_domain=from_domain,
        from_display_name="Test",
        to_email="me@gmail.com",
        subject="Test",
        snippet="Test body",
        headers={},
        received_at="2026-03-16T09:00:00Z",
    )


def _make_sender_data(
    email: str = "news@example.com",
    domain: str = "example.com",
    confidence: float = 0.9,
    user_pinned: bool = False,
    last_seen: str = "2026-03-16T10:00:00Z",
    action: str = "archive",
    category: str = "newsletter",
    label: str = "mailfiler/newsletter",
) -> dict[str, object]:
    return {
        "email": email,
        "domain": domain,
        "display_name": "Test",
        "category": category,
        "action": action,
        "label": label,
        "confidence": confidence,
        "source": "heuristic",
        "has_list_unsub": True,
        "has_precedence": "bulk",
        "dkim_valid": True,
        "spf_pass": True,
        "esp_fingerprint": None,
        "seen_count": 5,
        "correct_count": 3,
        "override_count": 0,
        "last_seen": last_seen,
        "first_seen": "2026-01-01T10:00:00Z",
        "user_pinned": user_pinned,
        "notes": None,
    }


def _make_domain_data(
    domain: str = "example.com",
    confidence: float = 0.9,
    user_pinned: bool = False,
    last_seen: str = "2026-03-16T10:00:00Z",
) -> dict[str, object]:
    return {
        "domain": domain,
        "category": "newsletter",
        "action": "archive",
        "label": "mailfiler/newsletter",
        "confidence": confidence,
        "source": "promoted",
        "seen_count": 10,
        "sender_count": 3,
        "last_seen": last_seen,
        "first_seen": "2026-01-01T10:00:00Z",
        "user_pinned": user_pinned,
    }


class TestCacheHit:
    def test_sender_match_above_threshold(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_sender_profile(conn, _make_sender_data(confidence=0.9))
        result = cache.lookup(_make_email(), conn)
        assert result is not None
        assert result.action is Action.ARCHIVE
        assert result.source is DecisionSource.CACHE_SENDER
        assert result.confidence == 0.9
        assert result.label == "mailfiler/newsletter"
        conn.close()

    def test_sender_below_threshold_falls_through(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_sender_profile(conn, _make_sender_data(confidence=0.5))
        result = cache.lookup(_make_email(), conn)
        assert result is None
        conn.close()


class TestCacheMiss:
    def test_unknown_sender_returns_none(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        email = _make_email(from_email="unknown@nowhere.com", from_domain="nowhere.com")
        result = cache.lookup(email, conn)
        assert result is None
        conn.close()


class TestDomainFallback:
    def test_domain_match_when_sender_missing(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_domain_profile(conn, _make_domain_data(confidence=0.9))
        result = cache.lookup(_make_email(), conn)
        assert result is not None
        assert result.source is DecisionSource.CACHE_DOMAIN
        conn.close()

    def test_domain_below_threshold_returns_none(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_domain_profile(conn, _make_domain_data(confidence=0.5))
        result = cache.lookup(_make_email(), conn)
        assert result is None
        conn.close()

    def test_sender_preferred_over_domain(self, tmp_db_path: Path) -> None:
        """When both sender and domain match, sender wins."""
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        sender = _make_sender_data(confidence=0.9, action="keep_inbox", label=None)
        upsert_sender_profile(conn, sender)
        upsert_domain_profile(conn, _make_domain_data(confidence=0.9))
        result = cache.lookup(_make_email(), conn)
        assert result is not None
        assert result.source is DecisionSource.CACHE_SENDER
        assert result.action is Action.KEEP_INBOX
        conn.close()


class TestConfidenceDecay:
    def test_no_decay_within_90_days(self, tmp_db_path: Path) -> None:
        """Senders seen within 90 days should not decay."""
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_sender_profile(conn, _make_sender_data(
            confidence=0.9,
            last_seen="2026-03-01T10:00:00Z",  # 15 days ago
        ))
        result = cache.lookup(_make_email(), conn, now="2026-03-16T10:00:00Z")
        assert result is not None
        assert result.confidence == 0.9
        conn.close()

    def test_decay_after_90_days(self, tmp_db_path: Path) -> None:
        """Senders not seen for >90 days should have decayed confidence."""
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_sender_profile(conn, _make_sender_data(
            confidence=0.9,
            last_seen="2025-12-01T10:00:00Z",  # ~106 days ago from 2026-03-16
        ))
        result = cache.lookup(_make_email(), conn, now="2026-03-16T10:00:00Z")
        # 106 - 90 = 16 days of decay: 0.9 * 0.98^16 ≈ 0.9 * 0.7238 ≈ 0.6514
        # Should fall below 0.85 threshold
        assert result is None
        conn.close()

    def test_decay_180_days_drops_significantly(self, tmp_db_path: Path) -> None:
        """After 180+ days, even high confidence should decay below threshold."""
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_sender_profile(conn, _make_sender_data(
            confidence=1.0,
            last_seen="2025-09-01T10:00:00Z",  # ~197 days ago
        ))
        result = cache.lookup(_make_email(), conn, now="2026-03-16T10:00:00Z")
        # 197 - 90 = 107 days of decay: 1.0 * 0.98^107 ≈ 0.1134
        assert result is None
        conn.close()


class TestPinning:
    def test_pinned_sender_always_matches(self, tmp_db_path: Path) -> None:
        """Pinned senders match regardless of confidence."""
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_sender_profile(conn, _make_sender_data(
            confidence=0.3,
            user_pinned=True,
        ))
        result = cache.lookup(_make_email(), conn)
        assert result is not None
        assert result.action is Action.ARCHIVE
        conn.close()

    def test_pinned_sender_ignores_decay(self, tmp_db_path: Path) -> None:
        """Pinned senders should not be subject to confidence decay."""
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.85)
        upsert_sender_profile(conn, _make_sender_data(
            confidence=0.9,
            user_pinned=True,
            last_seen="2025-06-01T10:00:00Z",  # very old
        ))
        result = cache.lookup(_make_email(), conn, now="2026-03-16T10:00:00Z")
        assert result is not None
        assert result.confidence == 0.9  # no decay applied
        conn.close()


class TestConfigurableThreshold:
    def test_custom_threshold(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.7)
        upsert_sender_profile(conn, _make_sender_data(confidence=0.75))
        result = cache.lookup(_make_email(), conn)
        assert result is not None
        conn.close()

    def test_custom_threshold_rejects_below(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        cache = CacheLayer(confidence_threshold=0.7)
        upsert_sender_profile(conn, _make_sender_data(confidence=0.65))
        result = cache.lookup(_make_email(), conn)
        assert result is None
        conn.close()
