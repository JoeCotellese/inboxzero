"""Implicit learning: detect user corrections from Gmail label changes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mailfiler.db.queries import (
    get_sender_profile,
    get_unreconciled_emails,
    mark_reconciled,
    upsert_sender_profile,
)
from mailfiler.feedback.corrections import apply_correction
from mailfiler.models import LearnedCorrection

if TYPE_CHECKING:
    import sqlite3

    from mailfiler.config import AppConfig
    from mailfiler.mail.protocol import MailClient

logger = logging.getLogger(__name__)


class LearningPhase:
    """Detect user corrections by comparing Gmail state to recorded decisions."""

    def learn(
        self,
        conn: sqlite3.Connection,
        mail_client: MailClient,
        config: AppConfig,
    ) -> list[LearnedCorrection]:
        """Check unreconciled emails for user corrections.

        For each unreconciled email, fetches current Gmail labels and compares
        to the recorded action. Detects:
        - Archived email moved back to inbox → keep_inbox
        - Inbox email removed from inbox → archive
        - Different mailfiler/* label → label change
        - No change → reconcile silently

        Returns list of detected corrections.
        """
        unreconciled = get_unreconciled_emails(conn)
        if not unreconciled:
            return []

        prefix = config.labels.prefix
        corrections: list[LearnedCorrection] = []

        for row in unreconciled:
            msg_id = row["gmail_message_id"]
            from_email = row["from_email"]
            old_action = row["action_taken"]
            old_label = row["label_applied"]

            try:
                current_labels = mail_client.get_message_labels(msg_id)
            except Exception:
                logger.warning("Failed to fetch labels for %s, skipping", msg_id)
                continue

            has_inbox = "INBOX" in current_labels
            current_mailfiler_labels = [
                lbl for lbl in current_labels if lbl.startswith(f"{prefix}/")
            ]
            # Exclude the mailfiler/inbox label from mailfiler labels comparison
            # since that's used for keep_inbox action, not a filing label
            current_filing_labels = [
                lbl for lbl in current_mailfiler_labels
                if lbl != f"{prefix}/inbox"
            ]

            correction = self._detect_correction(
                old_action=old_action,
                old_label=old_label,
                has_inbox=has_inbox,
                current_filing_labels=current_filing_labels,
                prefix=prefix,
            )

            if correction is None:
                # No change — just reconcile
                mark_reconciled(conn, msg_id)
                continue

            new_action, new_label = correction

            learned = LearnedCorrection(
                gmail_message_id=msg_id,
                from_email=from_email,
                old_action=old_action,
                new_action=new_action,
                old_label=old_label,
                new_label=new_label,
            )
            corrections.append(learned)

            # Apply correction via existing infrastructure
            apply_correction(conn, from_email)

            # Update sender profile with new action/label
            profile = get_sender_profile(conn, from_email)
            if profile is not None:
                data = dict(profile)
                data["action"] = new_action
                if new_label is not None:
                    data["label"] = new_label
                data["source"] = "user_learned"
                upsert_sender_profile(conn, data)

            mark_reconciled(conn, msg_id, learned_action=new_action)
            logger.info(
                "Learned correction for %s from %s: %s → %s",
                msg_id, from_email, old_action, new_action,
            )

        return corrections

    def _detect_correction(
        self,
        *,
        old_action: str,
        old_label: str | None,
        has_inbox: bool,
        current_filing_labels: list[str],
        prefix: str,
    ) -> tuple[str, str | None] | None:
        """Detect if user corrected the filing decision.

        Returns (new_action, new_label) if correction detected, None otherwise.
        """
        # Case 1: Email has INBOX label but we archived/labeled it → user wants inbox
        if has_inbox and old_action in ("archive", "label"):
            return ("keep_inbox", None)

        # Case 2: Email lost INBOX and was originally keep_inbox → user wants archive
        if not has_inbox and old_action == "keep_inbox":
            return ("archive", None)

        # Case 3: Different mailfiler/* label than what we set
        if current_filing_labels and old_label:
            current_label = current_filing_labels[0]
            if current_label != old_label:
                return ("archive", current_label)

        # No correction detected
        return None
