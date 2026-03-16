"""Pipeline processor — orchestrates the 3-layer triage pipeline."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

    from mailfiler.config import RunMode
    from mailfiler.mail.protocol import MailClient
    from mailfiler.models import EmailMessage
    from mailfiler.pipeline.cache import CacheLayer
    from mailfiler.pipeline.heuristics import HeuristicsLayer
    from mailfiler.pipeline.llm import LLMLayer

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
    ) -> None:
        self._mail = mail_client
        self._cache = cache_layer
        self._heuristics = heuristics_layer
        self._llm = llm_layer
        self._conn = conn
        self._run_mode = run_mode

    def process_email(self, email: EmailMessage) -> None:
        """Process a single email through the 3-layer pipeline.

        Placeholder — will be completed in Phase 7.
        """

    def process_batch(self, emails: list[EmailMessage]) -> int:
        """Process a batch of emails. Returns the count of successfully processed.

        Placeholder — will be completed in Phase 7.
        """
        processed = 0
        for email in emails:
            try:
                self.process_email(email)
                processed += 1
            except Exception:
                logger.exception("Failed to process email %s", email.gmail_message_id)
        return processed
