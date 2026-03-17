"""Pipeline processor — orchestrates the 3-layer triage pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mailfiler.config import RunMode
from mailfiler.db.queries import get_processed_email_by_gmail_id, upsert_processed_email
from mailfiler.models import Action, DecisionSource

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from mailfiler.config import AppConfig
    from mailfiler.mail.protocol import MailClient
    from mailfiler.models import EmailMessage
    from mailfiler.pipeline.cache import CacheLayer
    from mailfiler.pipeline.heuristics import HeuristicsLayer
    from mailfiler.pipeline.llm import LLMLayer


@dataclass(frozen=True)
class ProcessResult:
    """Result from processing a single email."""

    from_email: str
    subject: str | None
    action: Action
    label: str | None
    confidence: float
    decision_source: DecisionSource
    executed: bool
    llm_reason: str | None = None

logger = logging.getLogger(__name__)


class PipelineProcessor:
    """Orchestrates cache → heuristics → LLM pipeline."""

    def __init__(
        self,
        *,
        mail_client: MailClient,
        cache_layer: CacheLayer,
        heuristics_layer: HeuristicsLayer,
        llm_layer: LLMLayer,
        conn: sqlite3.Connection,
        run_mode: RunMode,
        config: AppConfig,
    ) -> None:
        self._mail = mail_client
        self._cache = cache_layer
        self._heuristics = heuristics_layer
        self._llm = llm_layer
        self._conn = conn
        self._run_mode = run_mode
        self._config = config

    def process_email(self, email: EmailMessage) -> ProcessResult | None:
        """Process a single email through the 3-layer pipeline.

        Returns None if the email was already processed (skip).
        Flow: cache → heuristics → LLM (if ambiguous)
        RunMode gates whether actions are executed or just logged.
        Audit log is written BEFORE action execution.
        """
        # Skip emails we've already processed
        existing = get_processed_email_by_gmail_id(self._conn, email.gmail_message_id)
        if existing is not None:
            logger.debug("Skipping already-processed email %s", email.gmail_message_id)
            return None

        action: Action
        label: str | None
        confidence: float
        decision_source: DecisionSource
        llm_category: str | None = None
        llm_reason: str | None = None

        # Layer 1: Cache lookup
        cache_result = self._cache.lookup(email, self._conn)
        if cache_result is not None:
            action = cache_result.action
            label = cache_result.label
            confidence = cache_result.confidence
            decision_source = cache_result.source
        else:
            # Layer 2: Heuristics
            heuristic_result = self._heuristics.score(email, self._config)

            if heuristic_result.is_override or heuristic_result.action is not Action.KEEP_INBOX:
                # Heuristic made a confident decision
                action = heuristic_result.action
                label = heuristic_result.label
                confidence = heuristic_result.confidence
                decision_source = DecisionSource.HEURISTIC
            else:
                # Layer 3: LLM for ambiguous cases
                llm_result = self._llm.classify(email)
                action = llm_result.action
                label = llm_result.label
                confidence = llm_result.confidence
                decision_source = DecisionSource.LLM
                llm_category = llm_result.category
                llm_reason = llm_result.reason

        # Log to audit table BEFORE executing action
        self._log_decision(
            email=email,
            action=action,
            label=label,
            confidence=confidence,
            decision_source=decision_source,
            llm_category=llm_category,
            llm_reason=llm_reason,
        )

        # Execute action based on RunMode
        executed = self._maybe_execute(email, action, label, decision_source)

        return ProcessResult(
            from_email=email.from_email,
            subject=email.subject,
            action=action,
            label=label,
            confidence=confidence,
            decision_source=decision_source,
            executed=executed,
            llm_reason=llm_reason,
        )

    def _maybe_execute(
        self,
        email: EmailMessage,
        action: Action,
        label: str | None,
        decision_source: DecisionSource,
    ) -> bool:
        """Execute the action if RunMode permits. Returns True if executed."""
        if self._run_mode is RunMode.OBSERVE:
            logger.info(
                "[observe] Would %s %s (%s)",
                action, email.gmail_message_id, decision_source,
            )
            return False

        if self._run_mode is RunMode.HEURISTICS_ONLY and decision_source is DecisionSource.LLM:
                logger.info(
                    "[heuristics_only] LLM suggests %s for %s — not executing",
                    action, email.gmail_message_id,
                )
                return False

        # Convert keep_inbox → archive into mailfiler/inbox label
        # Everything gets triaged out of raw Gmail inbox
        if action is Action.KEEP_INBOX:
            inbox_label = f"{self._config.labels.prefix}/inbox"
            self._mail.apply_action(email.gmail_message_id, Action.ARCHIVE, inbox_label)
        else:
            self._mail.apply_action(email.gmail_message_id, action, label)

        # Always mark as read — all processed emails leave the unread pool
        self._mail.apply_action(email.gmail_message_id, Action.MARK_READ)

        logger.info(
            "Executed %s on %s via %s",
            action, email.gmail_message_id, decision_source,
        )
        return True

    def _log_decision(
        self,
        *,
        email: EmailMessage,
        action: Action,
        label: str | None,
        confidence: float,
        decision_source: DecisionSource,
        llm_category: str | None,
        llm_reason: str | None,
    ) -> None:
        """Write decision to the processed_emails audit table."""
        upsert_processed_email(self._conn, {
            "gmail_message_id": email.gmail_message_id,
            "gmail_thread_id": email.gmail_thread_id,
            "from_email": email.from_email,
            "from_domain": email.from_domain,
            "subject": email.subject,
            "received_at": email.received_at,
            "processed_at": email.received_at,  # will be overridden with real time
            "action_taken": action.value,
            "label_applied": label,
            "decision_source": decision_source.value,
            "confidence": confidence,
            "llm_category": llm_category,
            "llm_reason": llm_reason,
            "was_overridden": False,
        })

    def process_batch(
        self,
        emails: list[EmailMessage],
        on_result: Callable[[ProcessResult], None] | None = None,
    ) -> int:
        """Process a batch of emails. Returns the count of successfully processed.

        One email failing doesn't abort the batch.
        If on_result is provided, it's called with each ProcessResult for live display.
        """
        processed = 0
        skipped = 0
        for email in emails:
            try:
                result = self.process_email(email)
                if result is None:
                    skipped += 1
                    continue
                processed += 1
                if on_result is not None:
                    on_result(result)
            except Exception:
                logger.exception(
                    "Failed to process email %s", email.gmail_message_id
                )
        if skipped:
            logger.info("Skipped %d already-processed emails", skipped)
        return processed
