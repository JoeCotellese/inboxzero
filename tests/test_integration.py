"""Integration tests: full pipeline with FakeMailClient, all RunModes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailfiler.config import AppConfig
from mailfiler.db.queries import (
    get_processed_email_by_gmail_id,
    get_sender_profile,
    upsert_sender_profile,
)
from mailfiler.db.schema import initialize_db
from mailfiler.feedback.corrections import apply_correction, promote_domains
from mailfiler.models import (
    Action,
    EmailMessage,
)
from mailfiler.pipeline.cache import CacheLayer
from mailfiler.pipeline.heuristics import HeuristicsLayer
from mailfiler.pipeline.llm import LLMClassification, LLMLayer
from mailfiler.pipeline.processor import PipelineProcessor
from tests.fakes import FakeMailClient

if TYPE_CHECKING:
    from pathlib import Path


def _make_email(
    gmail_message_id: str = "msg_001",
    from_email: str = "news@example.com",
    from_domain: str = "example.com",
    subject: str = "Test Subject",
    headers: dict[str, str] | None = None,
) -> EmailMessage:
    return EmailMessage(
        gmail_message_id=gmail_message_id,
        gmail_thread_id="thread_001",
        from_email=from_email,
        from_domain=from_domain,
        from_display_name="Test",
        to_email="joe@gmail.com",
        subject=subject,
        snippet="Test body",
        headers=headers or {},
        received_at="2026-03-16T09:00:00Z",
    )


def _make_config(run_mode: str = "full_auto") -> AppConfig:
    return AppConfig.model_validate({
        "gmail": {"credentials_file": "x", "token_file": "x"},
        "llm": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "rules": {},
        "vip_senders": {"emails": []},
        "vip_domains": {"domains": []},
        "blocked_senders": {"emails": []},
        "labels": {"prefix": "mailfiler"},
        "database": {},
        "daemon": {"run_mode": run_mode},
    })


class FakeLLMProvider:
    """Fake LLM that always returns archive with high confidence."""

    def classify(self, email: EmailMessage) -> LLMClassification:
        return LLMClassification(
            category="newsletter",
            priority="low",
            action=Action.ARCHIVE,
            label="mailfiler/archived",
            confidence=0.85,
            reason="LLM classified as low-priority",
        )


class TestFullPipelineObserveMode:
    """Full pipeline in observe mode: should log but never execute."""

    def test_observe_logs_but_does_not_execute(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        config = _make_config("observe")
        mail_client = FakeMailClient()
        processor = PipelineProcessor(
            mail_client=mail_client,
            cache_layer=CacheLayer(confidence_threshold=0.85),
            heuristics_layer=HeuristicsLayer(),
            llm_layer=LLMLayer(provider=FakeLLMProvider(), llm_threshold=0.6),
            conn=conn,
            run_mode=config.daemon.run_mode,
            config=config,
        )

        emails = [
            _make_email("msg_1", headers={"List-Unsubscribe": "<unsub>", "Precedence": "bulk"}),
            _make_email("msg_2", from_email="person@example.com", subject="Hello"),
        ]
        count = processor.process_batch(emails)
        assert count == 2
        # No actions should have been executed
        assert len(mail_client.applied_actions) == 0
        # But should be logged
        assert get_processed_email_by_gmail_id(conn, "msg_1") is not None
        assert get_processed_email_by_gmail_id(conn, "msg_2") is not None
        conn.close()


class TestFullPipelineHeuristicsOnly:
    """Heuristics-only mode: execute cache+heuristic, log LLM."""

    def test_heuristic_archive_executes(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        config = _make_config("heuristics_only")
        mail_client = FakeMailClient()
        processor = PipelineProcessor(
            mail_client=mail_client,
            cache_layer=CacheLayer(confidence_threshold=0.85),
            heuristics_layer=HeuristicsLayer(),
            llm_layer=LLMLayer(provider=FakeLLMProvider(), llm_threshold=0.6),
            conn=conn,
            run_mode=config.daemon.run_mode,
            config=config,
        )

        newsletter = _make_email(
            "msg_newsletter",
            headers={"List-Unsubscribe": "<unsub>", "Precedence": "bulk"},
        )
        processor.process_email(newsletter)
        # Should execute — heuristic confident enough
        assert len(mail_client.applied_actions) == 1
        conn.close()


class TestFullPipelineFullAuto:
    """Full auto: all layers execute."""

    def test_cache_hit_skips_other_layers(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        config = _make_config("full_auto")
        mail_client = FakeMailClient()

        # Seed a known sender in the cache
        upsert_sender_profile(conn, {
            "email": "known@example.com",
            "domain": "example.com",
            "display_name": "Known",
            "category": "newsletter",
            "action": "archive",
            "label": "mailfiler/newsletter",
            "confidence": 0.95,
            "source": "heuristic",
            "has_list_unsub": True,
            "has_precedence": "bulk",
            "dkim_valid": True,
            "spf_pass": True,
            "esp_fingerprint": None,
            "seen_count": 10,
            "correct_count": 8,
            "override_count": 0,
            "last_seen": "2026-03-16T09:00:00Z",
            "first_seen": "2026-01-01T10:00:00Z",
            "user_pinned": False,
            "notes": None,
        })

        processor = PipelineProcessor(
            mail_client=mail_client,
            cache_layer=CacheLayer(confidence_threshold=0.85),
            heuristics_layer=HeuristicsLayer(),
            llm_layer=LLMLayer(provider=FakeLLMProvider(), llm_threshold=0.6),
            conn=conn,
            run_mode=config.daemon.run_mode,
            config=config,
        )

        email = _make_email("msg_cached", from_email="known@example.com")
        processor.process_email(email)
        record = get_processed_email_by_gmail_id(conn, "msg_cached")
        assert record is not None
        assert record["decision_source"] == "cache:sender"
        assert len(mail_client.applied_actions) == 1
        conn.close()


class TestEmptyInbox:
    def test_empty_batch(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        config = _make_config("full_auto")
        mail_client = FakeMailClient()
        processor = PipelineProcessor(
            mail_client=mail_client,
            cache_layer=CacheLayer(confidence_threshold=0.85),
            heuristics_layer=HeuristicsLayer(),
            llm_layer=LLMLayer(provider=FakeLLMProvider(), llm_threshold=0.6),
            conn=conn,
            run_mode=config.daemon.run_mode,
            config=config,
        )
        count = processor.process_batch([])
        assert count == 0
        conn.close()


class TestFeedbackLoopIntegration:
    def test_correction_lowers_confidence(self, tmp_db_path: Path) -> None:
        """After a correction, sender confidence should drop."""
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, {
            "email": "promo@store.com",
            "domain": "store.com",
            "display_name": "Store",
            "category": "newsletter",
            "action": "archive",
            "label": "mailfiler/marketing",
            "confidence": 0.9,
            "source": "heuristic",
            "has_list_unsub": True,
            "has_precedence": None,
            "dkim_valid": True,
            "spf_pass": True,
            "esp_fingerprint": "mailchimp",
            "seen_count": 5,
            "correct_count": 3,
            "override_count": 0,
            "last_seen": "2026-03-16T09:00:00Z",
            "first_seen": "2026-02-01T10:00:00Z",
            "user_pinned": False,
            "notes": None,
        })
        apply_correction(conn, "promo@store.com")
        profile = get_sender_profile(conn, "promo@store.com")
        assert profile is not None
        assert profile["confidence"] < 0.9
        assert profile["override_count"] == 1
        conn.close()

    def test_domain_promotion_after_multiple_senders(self, tmp_db_path: Path) -> None:
        """3+ senders from same domain, same action → domain promoted."""
        conn = initialize_db(tmp_db_path)
        for i in range(3):
            upsert_sender_profile(conn, {
                "email": f"user{i}@bigco.com",
                "domain": "bigco.com",
                "display_name": f"User {i}",
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
                "last_seen": "2026-03-16T09:00:00Z",
                "first_seen": "2026-02-01T10:00:00Z",
                "user_pinned": False,
                "notes": None,
            })
        promoted = promote_domains(conn)
        assert "bigco.com" in promoted
        conn.close()
