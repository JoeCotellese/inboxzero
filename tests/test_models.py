"""Tests for domain models and enums."""

from __future__ import annotations

from mailfiler.models import (
    Action,
    CacheResult,
    Category,
    DecisionSource,
    EmailMessage,
    HeuristicResult,
    LLMClassification,
)


class TestEnums:
    def test_action_values(self) -> None:
        assert Action.ARCHIVE.value == "archive"
        assert Action.LABEL.value == "label"
        assert Action.KEEP_INBOX.value == "keep_inbox"
        assert Action.MARK_READ.value == "mark_read"
        assert Action.TRASH.value == "trash"

    def test_category_values(self) -> None:
        assert Category.NEWSLETTER.value == "newsletter"
        assert Category.TRANSACTIONAL.value == "transactional"
        assert Category.PERSON.value == "person"
        assert Category.NOTIFICATION.value == "notification"
        assert Category.VIP.value == "vip"
        assert Category.UNKNOWN.value == "unknown"

    def test_decision_source_values(self) -> None:
        assert DecisionSource.CACHE_SENDER.value == "cache:sender"
        assert DecisionSource.CACHE_DOMAIN.value == "cache:domain"
        assert DecisionSource.HEURISTIC.value == "heuristic"
        assert DecisionSource.LLM.value == "llm"


class TestEmailMessage:
    def test_construction(self) -> None:
        msg = EmailMessage(
            gmail_message_id="msg_001",
            gmail_thread_id="thread_001",
            from_email="test@example.com",
            from_domain="example.com",
            from_display_name="Test User",
            to_email="me@gmail.com",
            subject="Hello",
            snippet="Hello world...",
            headers={"List-Unsubscribe": "<mailto:unsub@example.com>"},
            received_at="2026-03-16T09:00:00Z",
        )
        assert msg.from_email == "test@example.com"
        assert msg.from_domain == "example.com"
        assert msg.headers["List-Unsubscribe"] == "<mailto:unsub@example.com>"

    def test_optional_fields_default_none(self) -> None:
        msg = EmailMessage(
            gmail_message_id="msg_002",
            gmail_thread_id=None,
            from_email="test@example.com",
            from_domain="example.com",
            from_display_name=None,
            to_email="me@gmail.com",
            subject=None,
            snippet=None,
            headers={},
            received_at=None,
        )
        assert msg.gmail_thread_id is None
        assert msg.from_display_name is None
        assert msg.subject is None


class TestCacheResult:
    def test_construction(self) -> None:
        result = CacheResult(
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.92,
            source=DecisionSource.CACHE_SENDER,
            category=Category.NEWSLETTER,
        )
        assert result.action is Action.ARCHIVE
        assert result.confidence == 0.92
        assert result.source is DecisionSource.CACHE_SENDER


class TestHeuristicResult:
    def test_construction(self) -> None:
        result = HeuristicResult(
            score=0.15,
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            category=Category.NEWSLETTER,
            confidence=0.92,
            applied_rules=["List-Unsubscribe present", "Precedence: bulk"],
            is_override=False,
        )
        assert result.score == 0.15
        assert len(result.applied_rules) == 2
        assert result.is_override is False

    def test_override(self) -> None:
        result = HeuristicResult(
            score=0.0,
            action=Action.KEEP_INBOX,
            label=None,
            category=Category.VIP,
            confidence=1.0,
            applied_rules=["X-PagerDuty override"],
            is_override=True,
        )
        assert result.is_override is True
        assert result.confidence == 1.0


class TestLLMClassification:
    def test_construction(self) -> None:
        result = LLMClassification(
            category="newsletter",
            priority="low",
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.85,
            reason="Bulk marketing email with unsubscribe link",
        )
        assert result.action is Action.ARCHIVE
        assert result.confidence == 0.85
        assert result.reason is not None
