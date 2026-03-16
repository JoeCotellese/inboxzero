"""Layer 1: Sender/domain cache lookup with confidence decay and pinning."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mailfiler.db.queries import get_domain_profile, get_sender_profile
from mailfiler.models import Action, CacheResult, Category, DecisionSource

if TYPE_CHECKING:
    import sqlite3

    from mailfiler.models import EmailMessage

_DECAY_RATE = 0.98
_GRACE_PERIOD_DAYS = 90


class CacheLayer:
    """Sender and domain cache with confidence decay and pinning."""

    def __init__(self, *, confidence_threshold: float = 0.85) -> None:
        self._threshold = confidence_threshold

    def lookup(
        self,
        email: EmailMessage,
        conn: sqlite3.Connection,
        *,
        now: str | None = None,
    ) -> CacheResult | None:
        """Look up sender, then domain. Returns None on miss.

        Args:
            email: The email to look up.
            conn: Database connection.
            now: ISO8601 timestamp for "now" (for testing decay). Defaults to utcnow.
        """
        current = _parse_time(now) if now else datetime.now(UTC)

        # Try exact sender match first
        sender = get_sender_profile(conn, email.from_email)
        if sender is not None:
            pinned = bool(sender["user_pinned"])
            confidence = float(sender["confidence"])

            if not pinned:
                confidence = _apply_decay(confidence, sender["last_seen"], current)

            if pinned or confidence >= self._threshold:
                return CacheResult(
                    action=Action(sender["action"]),
                    label=sender["label"],
                    confidence=confidence if not pinned else float(sender["confidence"]),
                    source=DecisionSource.CACHE_SENDER,
                    category=Category(sender["category"]),
                )

        # Fall back to domain
        domain = get_domain_profile(conn, email.from_domain)
        if domain is not None:
            pinned = bool(domain["user_pinned"])
            confidence = float(domain["confidence"])

            if not pinned:
                confidence = _apply_decay(confidence, domain["last_seen"], current)

            if pinned or confidence >= self._threshold:
                return CacheResult(
                    action=Action(domain["action"]),
                    label=domain["label"],
                    confidence=confidence if not pinned else float(domain["confidence"]),
                    source=DecisionSource.CACHE_DOMAIN,
                    category=Category(domain["category"]),
                )

        return None


def _parse_time(iso_str: str) -> datetime:
    """Parse an ISO8601 string to a timezone-aware datetime."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _apply_decay(stored_confidence: float, last_seen_str: str, now: datetime) -> float:
    """Apply time-based confidence decay.

    Formula: confidence * (0.98 ^ max(0, days_since_last_seen - 90))
    """
    last_seen = _parse_time(last_seen_str)
    days_since = (now - last_seen).days
    decay_days = max(0, days_since - _GRACE_PERIOD_DAYS)
    return stored_confidence * (_DECAY_RATE ** decay_days)
