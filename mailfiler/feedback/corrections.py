"""Correction detection, confidence adjustment, and domain promotion."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mailfiler.db.queries import (
    get_sender_profile,
    list_sender_profiles_for_domain,
    upsert_domain_profile,
    upsert_sender_profile,
)

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)

_OVERRIDE_DECAY = 0.6
_CONFIRMATION_BOOST = 0.05
_PIN_THRESHOLD = 3
_PROMOTION_MIN_SENDERS = 3
_PROMOTION_MIN_CONFIDENCE = 0.85


def apply_correction(conn: sqlite3.Connection, sender_email: str) -> None:
    """Apply a correction when a user overrides a filing decision.

    - confidence *= 0.6 (significant decay)
    - override_count += 1
    - If override_count >= 3, pin the sender
    """
    profile = get_sender_profile(conn, sender_email)
    if profile is None:
        logger.warning("Correction for unknown sender: %s", sender_email)
        return

    new_confidence = float(profile["confidence"]) * _OVERRIDE_DECAY
    new_override_count = int(profile["override_count"]) + 1
    pin = new_override_count >= _PIN_THRESHOLD

    # Build updated profile data
    data = dict(profile)
    data["confidence"] = new_confidence
    data["override_count"] = new_override_count
    if pin:
        data["user_pinned"] = True
        logger.info("Pinning sender %s after %d overrides", sender_email, new_override_count)

    upsert_sender_profile(conn, data)


def check_confirmations(
    conn: sqlite3.Connection,
    confirmed_emails: list[str],
) -> None:
    """Boost confidence for senders whose filings were not overridden.

    Called with a list of sender emails whose filed messages were not
    moved within the confirmation window (default 7 days).
    """
    for email in confirmed_emails:
        profile = get_sender_profile(conn, email)
        if profile is None:
            continue

        new_confidence = min(1.0, float(profile["confidence"]) + _CONFIRMATION_BOOST)
        new_correct_count = int(profile["correct_count"]) + 1

        data = dict(profile)
        data["confidence"] = new_confidence
        data["correct_count"] = new_correct_count
        upsert_sender_profile(conn, data)


def promote_domains(conn: sqlite3.Connection) -> list[str]:
    """Check for domains eligible for promotion.

    A domain is promoted when 3+ distinct senders from that domain
    share the same action with confidence >= 0.85.

    Returns:
        List of domain names that were promoted.
    """
    # Get all unique domains from sender profiles
    cursor = conn.execute("SELECT DISTINCT domain FROM sender_profiles")
    domains = [row[0] for row in cursor.fetchall()]

    promoted: list[str] = []

    for domain in domains:
        senders = list_sender_profiles_for_domain(conn, domain)
        if len(senders) < _PROMOTION_MIN_SENDERS:
            continue

        # Check for qualifying senders: same action, high confidence
        qualifying: dict[str, list[float]] = {}
        for sender in senders:
            if float(sender["confidence"]) >= _PROMOTION_MIN_CONFIDENCE:
                action = str(sender["action"])
                if action not in qualifying:
                    qualifying[action] = []
                qualifying[action].append(float(sender["confidence"]))

        for action, confidences in qualifying.items():
            if len(confidences) >= _PROMOTION_MIN_SENDERS:
                avg_confidence = sum(confidences) / len(confidences)
                # Find a representative sender for category/label
                representative = next(
                    s for s in senders
                    if str(s["action"]) == action
                    and float(s["confidence"]) >= _PROMOTION_MIN_CONFIDENCE
                )
                upsert_domain_profile(conn, {
                    "domain": domain,
                    "category": representative["category"],
                    "action": action,
                    "label": representative["label"],
                    "confidence": avg_confidence,
                    "source": "promoted",
                    "seen_count": sum(int(s["seen_count"]) for s in senders),
                    "sender_count": len(confidences),
                    "last_seen": max(str(s["last_seen"]) for s in senders),
                    "first_seen": min(str(s["first_seen"]) for s in senders),
                    "user_pinned": False,
                })
                promoted.append(domain)
                logger.info(
                    "Promoted domain %s with %d qualifying senders (avg confidence: %.2f)",
                    domain, len(confidences), avg_confidence,
                )
                break  # one promotion per domain

    return promoted
