"""SQLite schema creation for mailfiler."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS sender_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT UNIQUE NOT NULL,
    domain          TEXT NOT NULL,
    display_name    TEXT,

    -- Classification
    category        TEXT NOT NULL,
    action          TEXT NOT NULL,
    label           TEXT,
    confidence      REAL NOT NULL DEFAULT 0.5,
    source          TEXT NOT NULL,

    -- Header signals captured on first-seen
    has_list_unsub  INTEGER DEFAULT 0,
    has_precedence  TEXT,
    dkim_valid      INTEGER DEFAULT 0,
    spf_pass        INTEGER DEFAULT 0,
    esp_fingerprint TEXT,

    -- Learning metadata
    seen_count      INTEGER DEFAULT 1,
    correct_count   INTEGER DEFAULT 0,
    override_count  INTEGER DEFAULT 0,
    last_seen       TEXT NOT NULL,
    first_seen      TEXT NOT NULL,

    -- User control
    user_pinned     INTEGER DEFAULT 0,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_sender_email ON sender_profiles(email);
CREATE INDEX IF NOT EXISTS idx_sender_domain ON sender_profiles(domain);

CREATE TABLE IF NOT EXISTS domain_profiles (
    domain          TEXT PRIMARY KEY NOT NULL,
    category        TEXT NOT NULL,
    action          TEXT NOT NULL,
    label           TEXT,
    confidence      REAL NOT NULL DEFAULT 0.5,
    source          TEXT NOT NULL,
    seen_count      INTEGER DEFAULT 1,
    sender_count    INTEGER DEFAULT 1,
    last_seen       TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    user_pinned     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS processed_emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id TEXT UNIQUE NOT NULL,
    gmail_thread_id TEXT,
    from_email      TEXT NOT NULL,
    from_domain     TEXT NOT NULL,
    subject         TEXT,
    received_at     TEXT,
    processed_at    TEXT NOT NULL,
    action_taken    TEXT NOT NULL,
    label_applied   TEXT,
    decision_source TEXT NOT NULL,
    confidence      REAL,
    llm_category    TEXT,
    llm_reason      TEXT,
    was_overridden  INTEGER DEFAULT 0,
    reconciled_at   TEXT,
    learned_action  TEXT
);

CREATE INDEX IF NOT EXISTS idx_processed_gmail_id ON processed_emails(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_processed_from ON processed_emails(from_email);
CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_emails(processed_at);
"""


def _migrate_learning_columns(conn: sqlite3.Connection) -> None:
    """Add learning columns to processed_emails for existing databases.

    SQLite has no IF NOT EXISTS for ALTER TABLE ADD COLUMN, so we check
    PRAGMA table_info before altering.
    """
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(processed_emails)").fetchall()
    }
    if "reconciled_at" not in existing:
        conn.execute("ALTER TABLE processed_emails ADD COLUMN reconciled_at TEXT")
    if "learned_action" not in existing:
        conn.execute("ALTER TABLE processed_emails ADD COLUMN learned_action TEXT")
    # Index creation is idempotent via IF NOT EXISTS
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_processed_reconciled ON processed_emails(reconciled_at)"
    )


def initialize_db(db_path: Path) -> sqlite3.Connection:
    """Create or open the SQLite database and ensure schema exists.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A sqlite3.Connection with WAL mode and foreign keys enabled.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    _migrate_learning_columns(conn)
    return conn
