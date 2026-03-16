"""Tests for the pipeline processor — wiring all three layers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailfiler.config import AppConfig
from mailfiler.db.queries import get_processed_email_by_gmail_id
from mailfiler.db.schema import initialize_db
from mailfiler.models import (
    Action,
    CacheResult,
    Category,
    DecisionSource,
    EmailMessage,
    HeuristicResult,
    LLMClassification,
)
from mailfiler.pipeline.processor import PipelineProcessor
from tests.fakes import FakeMailClient

if TYPE_CHECKING:
    import sqlite3
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


class StubCacheLayer:
    """Cache layer that returns a fixed result or None."""

    def __init__(self, result: CacheResult | None = None) -> None:
        self._result = result

    def lookup(
        self, email: EmailMessage, conn: sqlite3.Connection, **kwargs: object
    ) -> CacheResult | None:
        return self._result


class StubHeuristicsLayer:
    """Heuristics layer that returns a fixed result."""

    def __init__(self, result: HeuristicResult | None = None) -> None:
        self._result = result or HeuristicResult(
            score=0.5,
            action=Action.KEEP_INBOX,
            label=None,
            category=Category.UNKNOWN,
            confidence=0.5,
            applied_rules=[],
            is_override=False,
        )

    def score(self, email: EmailMessage, config: AppConfig) -> HeuristicResult:
        return self._result


class StubLLMLayer:
    """LLM layer that returns a fixed result."""

    def __init__(self, result: LLMClassification | None = None) -> None:
        self._result = result or LLMClassification(
            category="fyi",
            priority="low",
            action=Action.ARCHIVE,
            label="mailfiler/archived",
            confidence=0.85,
            reason="Low priority",
        )

    def classify(self, email: EmailMessage) -> LLMClassification:
        return self._result


def _default_config(run_mode: str = "observe") -> AppConfig:
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


def _make_processor(
    *,
    tmp_db_path: Path,
    run_mode: str = "observe",
    cache_result: CacheResult | None = None,
    heuristic_result: HeuristicResult | None = None,
    llm_result: LLMClassification | None = None,
) -> tuple[PipelineProcessor, FakeMailClient, sqlite3.Connection]:
    conn = initialize_db(tmp_db_path)
    config = _default_config(run_mode)
    mail_client = FakeMailClient()
    processor = PipelineProcessor(
        mail_client=mail_client,
        cache_layer=StubCacheLayer(cache_result),
        heuristics_layer=StubHeuristicsLayer(heuristic_result),
        llm_layer=StubLLMLayer(llm_result),
        conn=conn,
        run_mode=config.daemon.run_mode,
        config=config,
    )
    return processor, mail_client, conn


class TestCacheHitPath:
    def test_cache_hit_logs_to_db(self, tmp_db_path: Path) -> None:
        cache_result = CacheResult(
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.92,
            source=DecisionSource.CACHE_SENDER,
            category=Category.NEWSLETTER,
        )
        processor, _mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="full_auto",
            cache_result=cache_result,
        )
        processor.process_email(_make_email())
        record = get_processed_email_by_gmail_id(conn, "msg_001")
        assert record is not None
        assert record["decision_source"] == "cache:sender"
        assert record["action_taken"] == "archive"
        conn.close()

    def test_cache_hit_applies_action_in_full_auto(self, tmp_db_path: Path) -> None:
        cache_result = CacheResult(
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.92,
            source=DecisionSource.CACHE_SENDER,
            category=Category.NEWSLETTER,
        )
        processor, mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="full_auto",
            cache_result=cache_result,
        )
        processor.process_email(_make_email())
        assert len(mail_client.applied_actions) == 1
        conn.close()


class TestHeuristicPath:
    def test_heuristic_archive(self, tmp_db_path: Path) -> None:
        """High-confidence heuristic should archive and log."""
        heuristic = HeuristicResult(
            score=0.1,
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            category=Category.NEWSLETTER,
            confidence=0.92,
            applied_rules=["List-Unsubscribe present"],
            is_override=False,
        )
        processor, mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="full_auto",
            heuristic_result=heuristic,
        )
        processor.process_email(_make_email())
        record = get_processed_email_by_gmail_id(conn, "msg_001")
        assert record is not None
        assert record["decision_source"] == "heuristic"
        assert len(mail_client.applied_actions) == 1
        conn.close()


class TestLLMPath:
    def test_ambiguous_heuristic_falls_to_llm(self, tmp_db_path: Path) -> None:
        """Ambiguous heuristic (0.25 < score < 0.85) should trigger LLM."""
        ambiguous = HeuristicResult(
            score=0.5,
            action=Action.KEEP_INBOX,
            label=None,
            category=Category.UNKNOWN,
            confidence=0.5,
            applied_rules=[],
            is_override=False,
        )
        llm = LLMClassification(
            category="newsletter",
            priority="low",
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.85,
            reason="Marketing email",
        )
        processor, _mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="full_auto",
            heuristic_result=ambiguous,
            llm_result=llm,
        )
        processor.process_email(_make_email())
        record = get_processed_email_by_gmail_id(conn, "msg_001")
        assert record is not None
        assert record["decision_source"] == "llm"
        conn.close()


class TestRunModeGating:
    def test_observe_mode_does_not_execute(self, tmp_db_path: Path) -> None:
        cache_result = CacheResult(
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.92,
            source=DecisionSource.CACHE_SENDER,
            category=Category.NEWSLETTER,
        )
        processor, mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="observe",
            cache_result=cache_result,
        )
        processor.process_email(_make_email())
        # Should log but not execute
        assert len(mail_client.applied_actions) == 0
        record = get_processed_email_by_gmail_id(conn, "msg_001")
        assert record is not None
        conn.close()

    def test_heuristics_only_executes_cache_and_heuristic(self, tmp_db_path: Path) -> None:
        heuristic = HeuristicResult(
            score=0.1,
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            category=Category.NEWSLETTER,
            confidence=0.92,
            applied_rules=["List-Unsubscribe present"],
            is_override=False,
        )
        processor, mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="heuristics_only",
            heuristic_result=heuristic,
        )
        processor.process_email(_make_email())
        # Should execute heuristic action
        assert len(mail_client.applied_actions) == 1
        conn.close()

    def test_heuristics_only_does_not_execute_llm(self, tmp_db_path: Path) -> None:
        ambiguous = HeuristicResult(
            score=0.5,
            action=Action.KEEP_INBOX,
            label=None,
            category=Category.UNKNOWN,
            confidence=0.5,
            applied_rules=[],
            is_override=False,
        )
        llm = LLMClassification(
            category="newsletter",
            priority="low",
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.85,
            reason="Marketing",
        )
        processor, mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="heuristics_only",
            heuristic_result=ambiguous,
            llm_result=llm,
        )
        processor.process_email(_make_email())
        # LLM result should be logged but not executed
        assert len(mail_client.applied_actions) == 0
        conn.close()


class TestBatchProcessing:
    def test_batch_processes_all(self, tmp_db_path: Path) -> None:
        cache_result = CacheResult(
            action=Action.ARCHIVE,
            label="mailfiler/newsletter",
            confidence=0.92,
            source=DecisionSource.CACHE_SENDER,
            category=Category.NEWSLETTER,
        )
        processor, _mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="full_auto",
            cache_result=cache_result,
        )
        emails = [_make_email(f"msg_{i}") for i in range(5)]
        count = processor.process_batch(emails)
        assert count == 5
        conn.close()

    def test_batch_continues_on_error(self, tmp_db_path: Path) -> None:
        """One email failing should not abort the batch."""
        processor, _mail_client, conn = _make_processor(
            tmp_db_path=tmp_db_path,
            run_mode="full_auto",
        )
        emails = [_make_email(f"msg_{i}") for i in range(3)]
        count = processor.process_batch(emails)
        # All should process (default stubs return keep_inbox for ambiguous)
        assert count == 3
        conn.close()
