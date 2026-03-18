"""Database query functions for mailfiler.

All functions take a sqlite3.Connection as their first parameter
and use parameterized queries exclusively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3


def upsert_sender_profile(conn: sqlite3.Connection, data: dict[str, object]) -> None:
    """Insert or update a sender profile by email."""
    conn.execute(
        """\
        INSERT INTO sender_profiles (
            email, domain, display_name, category, action, label, confidence,
            source, has_list_unsub, has_precedence, dkim_valid, spf_pass,
            esp_fingerprint, seen_count, correct_count, override_count,
            last_seen, first_seen, user_pinned, notes
        ) VALUES (
            :email, :domain, :display_name, :category, :action, :label, :confidence,
            :source, :has_list_unsub, :has_precedence, :dkim_valid, :spf_pass,
            :esp_fingerprint, :seen_count, :correct_count, :override_count,
            :last_seen, :first_seen, :user_pinned, :notes
        )
        ON CONFLICT(email) DO UPDATE SET
            domain = excluded.domain,
            display_name = excluded.display_name,
            category = excluded.category,
            action = excluded.action,
            label = excluded.label,
            confidence = excluded.confidence,
            source = excluded.source,
            has_list_unsub = excluded.has_list_unsub,
            has_precedence = excluded.has_precedence,
            dkim_valid = excluded.dkim_valid,
            spf_pass = excluded.spf_pass,
            esp_fingerprint = excluded.esp_fingerprint,
            seen_count = excluded.seen_count,
            correct_count = excluded.correct_count,
            override_count = excluded.override_count,
            last_seen = excluded.last_seen,
            user_pinned = excluded.user_pinned,
            notes = excluded.notes
        """,
        data,
    )
    conn.commit()


def get_sender_profile(
    conn: sqlite3.Connection, email: str
) -> sqlite3.Row | None:
    """Fetch a sender profile by email address."""
    cursor = conn.execute(
        "SELECT * FROM sender_profiles WHERE email = ?",
        (email,),
    )
    return cursor.fetchone()


def delete_sender_profile(conn: sqlite3.Connection, email: str) -> bool:
    """Delete a sender profile by email. Returns True if a row was deleted."""
    cursor = conn.execute(
        "DELETE FROM sender_profiles WHERE email = ?",
        (email,),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_sender_profiles_for_domain(
    conn: sqlite3.Connection, domain: str
) -> list[sqlite3.Row]:
    """List all sender profiles for a given domain."""
    cursor = conn.execute(
        "SELECT * FROM sender_profiles WHERE domain = ?",
        (domain,),
    )
    return cursor.fetchall()


def upsert_domain_profile(conn: sqlite3.Connection, data: dict[str, object]) -> None:
    """Insert or update a domain profile."""
    conn.execute(
        """\
        INSERT INTO domain_profiles (
            domain, category, action, label, confidence, source,
            seen_count, sender_count, last_seen, first_seen, user_pinned
        ) VALUES (
            :domain, :category, :action, :label, :confidence, :source,
            :seen_count, :sender_count, :last_seen, :first_seen, :user_pinned
        )
        ON CONFLICT(domain) DO UPDATE SET
            category = excluded.category,
            action = excluded.action,
            label = excluded.label,
            confidence = excluded.confidence,
            source = excluded.source,
            seen_count = excluded.seen_count,
            sender_count = excluded.sender_count,
            last_seen = excluded.last_seen,
            user_pinned = excluded.user_pinned
        """,
        data,
    )
    conn.commit()


def get_domain_profile(
    conn: sqlite3.Connection, domain: str
) -> sqlite3.Row | None:
    """Fetch a domain profile by domain name."""
    cursor = conn.execute(
        "SELECT * FROM domain_profiles WHERE domain = ?",
        (domain,),
    )
    return cursor.fetchone()


def upsert_processed_email(conn: sqlite3.Connection, data: dict[str, object]) -> None:
    """Insert or update a processed email record."""
    conn.execute(
        """\
        INSERT INTO processed_emails (
            gmail_message_id, gmail_thread_id, from_email, from_domain,
            subject, received_at, processed_at, action_taken, label_applied,
            decision_source, confidence, llm_category, llm_reason, was_overridden
        ) VALUES (
            :gmail_message_id, :gmail_thread_id, :from_email, :from_domain,
            :subject, :received_at, :processed_at, :action_taken, :label_applied,
            :decision_source, :confidence, :llm_category, :llm_reason, :was_overridden
        )
        ON CONFLICT(gmail_message_id) DO UPDATE SET
            action_taken = excluded.action_taken,
            label_applied = excluded.label_applied,
            decision_source = excluded.decision_source,
            confidence = excluded.confidence,
            llm_category = excluded.llm_category,
            llm_reason = excluded.llm_reason,
            was_overridden = excluded.was_overridden
        """,
        data,
    )
    conn.commit()


def get_processed_email_by_gmail_id(
    conn: sqlite3.Connection, gmail_message_id: str
) -> sqlite3.Row | None:
    """Fetch a processed email by Gmail message ID."""
    cursor = conn.execute(
        "SELECT * FROM processed_emails WHERE gmail_message_id = ?",
        (gmail_message_id,),
    )
    return cursor.fetchone()


def list_processed_emails(
    conn: sqlite3.Connection, limit: int = 50
) -> list[sqlite3.Row]:
    """List processed emails, most recent first."""
    cursor = conn.execute(
        "SELECT * FROM processed_emails ORDER BY processed_at DESC LIMIT ?",
        (limit,),
    )
    return cursor.fetchall()


def get_unreconciled_emails(
    conn: sqlite3.Connection, limit: int = 200
) -> list[sqlite3.Row]:
    """Return processed emails that haven't been reconciled yet."""
    cursor = conn.execute(
        "SELECT * FROM processed_emails WHERE reconciled_at IS NULL LIMIT ?",
        (limit,),
    )
    return cursor.fetchall()


def mark_reconciled(
    conn: sqlite3.Connection,
    gmail_message_id: str,
    learned_action: str | None = None,
) -> None:
    """Mark a processed email as reconciled, optionally recording the learned action."""
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc).isoformat()
    conn.execute(
        """\
        UPDATE processed_emails
        SET reconciled_at = ?, learned_action = ?
        WHERE gmail_message_id = ?
        """,
        (now, learned_action, gmail_message_id),
    )
    conn.commit()


def list_learned_corrections(
    conn: sqlite3.Connection, limit: int = 50
) -> list[sqlite3.Row]:
    """List emails where learning detected a user correction."""
    cursor = conn.execute(
        """\
        SELECT * FROM processed_emails
        WHERE learned_action IS NOT NULL
        ORDER BY reconciled_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()
